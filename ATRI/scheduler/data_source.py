import asyncio
import contextlib
import inspect
import logging
from typing import ClassVar

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_MISSED
from apscheduler.job import Job
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.date import DateTrigger
from nonebot.log import LoguruHandler

from ATRI.exceptions import BotRuntimeError, str_traceback

# APScheduler 默认 misfire_grace_time 只有 1 秒，事件循环抖一下就整次跳过；
# 统一放宽到 60 秒，按天推进的任务再各自显式放宽。
scheduler = AsyncIOScheduler(
    timezone="Asia/Shanghai",
    job_defaults={"misfire_grace_time": 60, "coalesce": True, "max_instances": 1},
)

#: ``run_job_now`` 对原本暂停的任务打标记，跑完这一次再暂停回去。
_pause_after_run: set[str] = set()


def _restore_paused_after_run(event) -> None:
    """``run_job_now`` 的收尾：只跑一次的任务执行完后恢复暂停状态。"""
    job_id = getattr(event, "job_id", None)
    if not job_id or job_id not in _pause_after_run:
        return
    _pause_after_run.discard(job_id)
    # 一次性任务跑完就从调度器里摘掉了，暂停失败是正常情况。
    with contextlib.suppress(Exception):
        scheduler.pause_job(job_id)


scheduler.add_listener(
    _restore_paused_after_run,
    EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED,
)

aps_logger = logging.getLogger("apscheduler")
aps_logger.setLevel(30)
aps_logger.handlers.clear()
aps_logger.addHandler(LoguruHandler())


class SchedulerJob:
    """计划任务对象"""

    def __init__(
        self,
        func,
        id: str,
        trigger: str | BaseTrigger = "date",
        service: str = "",
        name: str = "",
        **kwargs,
    ):
        self.id = id
        self.service = service
        self.name = name or id
        self.job: Job = scheduler.add_job(
            func=func, trigger=trigger, id=id, name=self.name, **kwargs
        )

    @property
    def is_paused(self) -> bool:
        """任务是否处于暂停状态。

        与 :meth:`status` 保持同一口径：``next_run_time`` 为空即视为暂停。
        """
        return getattr(self.job, "next_run_time", None) is None

    @property
    def next_run_time(self):
        """下次执行时间；暂停或调度器尚未启动时为 ``None``。"""
        return getattr(self.job, "next_run_time", None)

    @property
    def trigger_text(self) -> str:
        """触发器的可读描述，例如 ``cron[hour='4', minute='0']``。"""
        trigger = getattr(self.job, "trigger", None)
        return str(trigger) if trigger is not None else "unknown"

    def status(self):
        if hasattr(self.job, "next_run_time"):
            status = (
                "next run at: "
                + self.job.next_run_time.strftime("%Y-%m-%d %H:%M:%S %Z")
                if self.job.next_run_time
                else "paused"
            )
        else:
            status = "pending"
        return status

    def pause(self):
        self.job.pause()

    def resume(self):
        self.job.resume()


class SchedulerController:
    """服务的计划控制器"""

    service_schedulers: ClassVar[dict[str, dict[str, SchedulerJob]]] = {}

    def __init__(self, service: str):
        self.service = service
        if service in self.service_schedulers:
            return
        self.service_schedulers[service] = {}

    def add_job(
        self,
        func,
        name: str,
        trigger: str | BaseTrigger = "date",
        args: list | None = None,
        kwargs: dict | None = None,
        use_log: bool = True,
        persist: bool = False,
        **job_kwargs,
    ) -> SchedulerJob:
        """添加计划任务，使用方法同 apscheduler，并支持传递参数给任务函数。

        :param persist: 是否把这个任务写进数据库，重启后由
            :meth:`restore` 重建。默认不持久化——系统的高频心跳
            （每分钟一次）没必要每次启动都读一遍库。
            对用户可见的承诺类任务（「十分钟后提醒你」）必须开启，
            否则机器人重启就等于失信。
        """
        if name in self.service_schedulers[self.service]:
            raise BotRuntimeError(
                f"创建服务`{self.service}`的任务`{name}`失败：该任务名称已存在"
            )
        if args is not None:
            job_kwargs["args"] = args
        if kwargs is not None:
            job_kwargs["kwargs"] = kwargs
        if "id" in job_kwargs:
            job_kwargs.pop("id")
        is_one_time = trigger == "date" or isinstance(trigger, DateTrigger)
        job_ref: dict[str, SchedulerJob] = {}

        def job_func(f):
            from ATRI.log import log

            if inspect.iscoroutinefunction(f):

                async def wrapper(*args, **kwargs):
                    try:
                        if use_log:
                            log.debug(f"开始执行`{self.service}`的任务`{name}`")
                        await f(*args, **kwargs)
                        if use_log:
                            log.debug(f"`{self.service}`的任务`{name}`执行完毕")
                    except Exception as e:
                        log.error(
                            f"在执行`{self.service}`的任务`{name}`时失败:\n{str_traceback(e)}"
                        )
                    finally:
                        if is_one_time and self.service_schedulers[self.service].get(
                            name
                        ) is job_ref.get("job"):
                            self.service_schedulers[self.service].pop(name, None)

                return wrapper
            else:

                async def wrapper(*args, **kwargs):
                    try:
                        if use_log:
                            log.debug(f"开始执行`{self.service}`的任务`{name}`")
                        await asyncio.to_thread(f, *args, **kwargs)
                        if use_log:
                            log.debug(f"`{self.service}`的任务`{name}`执行完毕")
                    except Exception as e:
                        log.error(
                            f"在执行`{self.service}`的任务`{name}`时失败:\n{str_traceback(e)}"
                        )
                    finally:
                        if is_one_time and self.service_schedulers[self.service].get(
                            name
                        ) is job_ref.get("job"):
                            self.service_schedulers[self.service].pop(name, None)

                return wrapper

        job = SchedulerJob(
            func=job_func(func),
            trigger=trigger,
            id=f"{self.service}-{name}",
            service=self.service,
            name=name,
            **job_kwargs,
        )
        self.service_schedulers[self.service][name] = job
        job_ref["job"] = job
        if persist:
            persist_kwargs = dict(job_kwargs)
            if kwargs is not None:
                persist_kwargs["kwargs"] = kwargs
            _spawn(_persist_job(self.service, name, trigger, persist_kwargs))
        return job

    async def add_job_async(
        self,
        func,
        name: str,
        trigger: str | BaseTrigger = "date",
        args: list | None = None,
        kwargs: dict | None = None,
        use_log: bool = True,
        persist: bool = False,
        **job_kwargs,
    ) -> SchedulerJob:
        """`add_job` 的异步版本：注册任务，并在需要时**可靠地**写库。

        与同步版的差别只有持久化这一步：这里 `await _persist_job(...)`，
        确保任务定义真的落进数据库。同步版用后台任务写库，
        在 AnyIO worker 线程里会静默丢失。

        面向用户创建的定时任务（「十分钟后提醒我」）必须用这个版本，
        否则重启后恢复不出来。
        """
        job = self.add_job(
            func,
            name,
            trigger,
            args=args,
            kwargs=kwargs,
            use_log=use_log,
            persist=False,
            **job_kwargs,
        )
        if persist:
            persist_kwargs = dict(job_kwargs)
            if kwargs is not None:
                persist_kwargs["kwargs"] = kwargs
            ok = await _persist_job(self.service, name, trigger, persist_kwargs)
            if not ok:
                import contextlib

                with contextlib.suppress(Exception):
                    self.remove_job(name, forget=False)
                raise BotRuntimeError(f"任务`{name}`持久化失败，已撤回注册")
        return job

    def add_pause_job(
        self, func, name: str, trigger: str | BaseTrigger = "date", **kwargs
    ) -> SchedulerJob:
        """添加暂停的计划任务；``kwargs`` 原样传给 :meth:`add_job`。"""
        job = self.add_job(func, name, trigger, **kwargs)
        job.pause()
        return job

    def get_job(self, name) -> SchedulerJob:
        """获取SchedulerJob对象"""
        if not self.has_job(name):
            raise BotRuntimeError(f"找不到服务`{self.service}`的任务`{name}`")
        return self.service_schedulers[self.service][name]

    def remove_job(self, name, *, forget: bool = True):
        """移除指定Job。

        :param forget: 是否同时删掉持久化记录。默认删——
            移除任务的意思就是「不再执行」，留着记录会导致下次启动又冒出来。
        """
        if not self.has_job(name):
            raise BotRuntimeError(f"找不到服务`{self.service}`的任务`{name}`")
        job = self.get_job(name)
        if scheduler.get_job(job_id=job.id) is not None:
            job.job.remove()
        del self.service_schedulers[self.service][name]
        if forget:
            _forget_job(self.service, name)

    def has_job(self, name):
        """判断是否存在指定Job"""
        return (
            self.service in self.service_schedulers
            and name in self.service_schedulers[self.service]
        )

    @classmethod
    def list_jobs(cls, service: str | None = None) -> list[SchedulerJob]:
        """列出计划任务；``service`` 为空时返回全部服务的任务。"""
        if service is not None:
            return list(cls.service_schedulers.get(service, {}).values())
        return [
            job for jobs in cls.service_schedulers.values() for job in jobs.values()
        ]

    def pause_job(self, name: str) -> SchedulerJob:
        """暂停指定任务。"""
        job = self.get_job(name)
        job.pause()
        return job

    def resume_job(self, name: str) -> SchedulerJob:
        """恢复指定任务。"""
        job = self.get_job(name)
        job.resume()
        return job

    def run_job_now(self, name: str) -> SchedulerJob:
        """立即执行一次指定任务。

        通过 ``resume()`` + 修改 ``next_run_time`` 让调度器马上跑一遍，
        **不直接调用任务函数**——这样日志包装、异常兜底和一次性任务的
        清理逻辑都还在。

        原本暂停的任务跑完这一次会重新暂停（见 ``_restore_paused_after_run``）：
        「立即执行一次」不等于「恢复这个任务」。
        """
        from ATRI.utils.datetime import now

        job = self.get_job(name)
        if scheduler.get_job(job.id) is None:
            raise BotRuntimeError(f"任务`{name}`已过期，无法执行")
        was_paused = job.is_paused
        if was_paused:
            _pause_after_run.add(job.id)
            job.resume()
        if scheduler.get_job(job.id) is None:
            _pause_after_run.discard(job.id)
            raise BotRuntimeError(f"任务`{name}`已过期，无法执行")
        try:
            job.job.modify(next_run_time=now())
        except Exception:
            _pause_after_run.discard(job.id)
            if was_paused:
                job.pause()
            raise
        return job

    @classmethod
    def get_job_by_id(cls, job_id: str) -> Job | None:
        """按 job id 取 APScheduler 的 Job；不存在时返回 ``None``。

        调用方（如 WebAPI）要自己判空——之前直接 ``.pause()``，
        取不到就抛 ``AttributeError``，被外层宽 ``except`` 吞成通用错误。
        """
        return scheduler.get_job(job_id=job_id)


#: 持有后台任务的引用，防止被 GC 回收（RUF006）。
#: 任务完成后由回调自动移除。
_pending_writes: set = set()


def _spawn(coro) -> None:
    """把写库协程交给**调度器自己的事件循环**执行，实在没有循环就同步跑完。

    之前用 ``asyncio.get_event_loop()``，在 AnyIO worker 线程里直接抛
    「no current event loop」，异常又被 debug 日志吞掉——结果是任务注册了、
    库里没有。现在按可用性依次退让，任何一条路都保证协程被执行。
    """
    from ATRI.log import log

    scheduler_loop = getattr(scheduler, "_eventloop", None)
    if scheduler_loop is not None and scheduler_loop.is_closed():
        scheduler_loop = None

    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if scheduler_loop is not None and running_loop is scheduler_loop:
        task = scheduler_loop.create_task(_guard_write(coro))
        _pending_writes.add(task)
        task.add_done_callback(_pending_writes.discard)
        return

    if scheduler_loop is not None and scheduler_loop.is_running():
        future = asyncio.run_coroutine_threadsafe(_guard_write(coro), scheduler_loop)
        _pending_writes.add(future)
        future.add_done_callback(_pending_writes.discard)
        return

    if running_loop is not None:
        task = running_loop.create_task(_guard_write(coro))
        _pending_writes.add(task)
        task.add_done_callback(_pending_writes.discard)
        return

    # 没有循环可用（worker 线程或启动前的同步代码）：同步跑完，慢一点但不会丢。
    try:
        asyncio.run(_guard_write(coro))
    except Exception as exc:
        log.debug(f"调度持久化任务失败：{exc}")


async def _guard_write(coro) -> None:
    """执行写库协程，失败只记日志。"""
    try:
        await coro
    except Exception as exc:  # pragma: no cover - 兜底
        from ATRI.log import log

        log.debug(f"定时任务持久化失败：{exc}")


def _split_job_options(job_kwargs: dict) -> dict:
    """挑出属于 job、不属于触发器的参数，供恢复时原样交回。

    按「是否出现过」判断，不按值判断：``misfire_grace_time=None`` 在
    APScheduler 里是「迟到多久都照跑」，和缺省是两回事。
    """
    keys = ("misfire_grace_time", "max_instances", "coalesce", "executor")
    return {k: job_kwargs[k] for k in keys if k in job_kwargs}


def _serialize_trigger(trigger, job_kwargs: dict) -> tuple[str, dict]:
    """把触发器转成 (kind, args) 以便入库。看不懂的类型返回空。

    job 级参数（迟到窗口、并发数、合并）压在 ``__job_options__`` 下一起存，
    恢复时再拆出来——否则重启后这些值会退回全局默认，
    「原定 300 秒内补跑」变成 60 秒。
    """
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.date import DateTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    def _pack(kind: str, args: dict) -> tuple[str, dict]:
        options = _split_job_options(job_kwargs)
        if options:
            args["__job_options__"] = options
        return kind, args

    if isinstance(trigger, str):
        args = {
            k: v
            for k, v in job_kwargs.items()
            if k not in ("args", "kwargs", "id", "trigger")
            and k not in ("misfire_grace_time", "max_instances", "coalesce", "executor")
            and k not in ("next_run_time", "replace_existing", "jobstore")
        }
        return _pack(trigger, args)

    if isinstance(trigger, DateTrigger):
        if trigger.run_date is None:
            return "", {}
        return _pack("date", {"run_date": trigger.run_date.isoformat()})
    if isinstance(trigger, CronTrigger):
        args = {}
        for f in trigger.fields:
            text = str(f)
            if text == "*":
                continue
            try:
                args[f.name] = int(text)
            except ValueError:
                args[f.name] = text
        for key in ("jitter", "start_date", "end_date"):
            value = getattr(trigger, key, None)
            if value is not None:
                args[key] = value.isoformat() if hasattr(value, "isoformat") else value
        return _pack("cron", args)
    if isinstance(trigger, IntervalTrigger):
        args = {"seconds": int(trigger.interval.total_seconds())}
        jitter = getattr(trigger, "jitter", None)
        if jitter:
            args["jitter"] = jitter
        end_date = getattr(trigger, "end_date", None)
        if end_date is not None:
            args["end_date"] = end_date.isoformat()
        return _pack("interval", args)
    return "", {}


async def _persist_job(service: str, name: str, trigger, job_kwargs: dict) -> bool:
    """把任务定义写入数据库（若可序列化）。返回是否写入成功。

    ``add_job(persist=True)`` 走后台任务调用它，``add_job_async`` 直接 await。
    想确保写库成功，用后者。
    """
    import json
    import time

    kind, trigger_args = _serialize_trigger(trigger, job_kwargs)
    if not kind:
        from ATRI.log import log

        log.debug(f"任务 {service}:{name} 的触发器不支持持久化，跳过")
        return False

    payload_kwargs = job_kwargs.get("kwargs") or {}
    try:
        kwargs_text = json.dumps(payload_kwargs, ensure_ascii=False)
    except (TypeError, ValueError):
        from ATRI.log import log

        log.debug(f"任务 {service}:{name} 的参数不可 JSON 化，跳过持久化")
        return False

    from ATRI.log import log

    try:
        from .model import SchedulerTask

        await SchedulerTask.update_or_create(
            key=f"{service}:{name}",
            defaults={
                "service": service,
                "name": name,
                "trigger": kind,
                "trigger_args": json.dumps(trigger_args, ensure_ascii=False),
                "job_kwargs": kwargs_text,
                "created_at": time.time(),
            },
        )
        return True
    except Exception as exc:
        log.warning(f"任务 {service}:{name} 持久化失败：{exc}")
        return False


def _forget_job(service: str, name: str) -> None:
    """从数据库删掉任务定义。"""

    async def _delete():
        from .model import SchedulerTask

        await SchedulerTask.filter(key=f"{service}:{name}").delete()

    _spawn(_delete())


async def load_persisted() -> list[dict]:
    """读出所有已持久化的任务定义，供调用方重建。

    返回的每项含 ``service`` / ``name`` / ``trigger`` / ``trigger_args`` /
    ``job_kwargs``。**函数不在其中**——调用方必须自己带着同一个函数
    调用 `add_job(..., persist=True)`。
    """
    import json

    from .model import SchedulerTask

    rows = await SchedulerTask.all()
    out = []
    for row in rows:
        try:
            args = json.loads(row.trigger_args or "{}")
            kwargs = json.loads(row.job_kwargs or "{}")
        except (TypeError, ValueError):
            continue
        out.append(
            {
                "service": row.service,
                "name": row.name,
                "trigger": row.trigger,
                "trigger_args": args,
                "job_kwargs": kwargs,
            }
        )
    return out


async def restore(funcs: dict[str, object], *, drop_expired: bool = False) -> int:
    """**由核心调度器负责**把持久化的任务重新注册回来。返回成功数量。

    为什么恢复逻辑必须在这里：任务定义是 `SchedulerController` 自己写进库的
    （`add_job(persist=True)`），读回来也该由它负责。让各个服务自己实现，
    就必然出现「存了但没人读」——本方法存在之前正是如此。

    :param funcs: ``{任务名: 可调用对象}`` 映射。库里只存名字与参数，
        函数对象无法序列化，只能由调用方在启动时提供。
        找不到对应函数的任务会跳过（函数可能已被删除）。
    :param drop_expired: 一次性任务若时间点已过，是否**直接删除**。
        默认否——过期只说明「到点时机器人不在线」，承诺还在，
        应当立即补一次而不是当她没答应过。只有确实不要补发时才传 True。
    """
    restored = 0
    for item in await load_persisted():
        name = item["name"]
        service = item["service"]
        func = funcs.get(name)
        if func is None:
            continue
        trigger = item["trigger"]
        args = dict(item["trigger_args"])
        kwargs = item["job_kwargs"] or {}
        job_options = args.pop("__job_options__", None) or {}
        if trigger == "date":
            run_date = str(args.get("run_date") or "")
            if _is_past(run_date):
                from ATRI.log import log

                if drop_expired:
                    _forget_job(service, name)
                    log.debug(f"任务 {service}:{name} 已过期，丢弃")
                    continue
                restored += 1
                log.info(f"任务 {service}:{name} 已过期，立即补发一次")
                _spawn(_run_expired(func, kwargs, service, name))
                continue
        controller = SchedulerController(service)
        try:
            if trigger == "date":
                from datetime import datetime

                from apscheduler.triggers.date import DateTrigger

                trig = DateTrigger(run_date=datetime.fromisoformat(run_date))
                controller.add_job(
                    func, name, trig, kwargs=kwargs, persist=False, **job_options
                )
            else:
                controller.add_job(
                    func,
                    name,
                    trigger,
                    kwargs=kwargs,
                    persist=False,
                    **args,
                    **job_options,
                )
            restored += 1
        except Exception as exc:
            from ATRI.log import log

            log.warning(f"恢复任务 {service}:{name} 失败：{exc}")
            continue

    if restored:
        from ATRI.log import log

        log.info(f"已恢复 {restored} 个持久化定时任务")
    return restored


async def _run_expired(func, kwargs: dict, service: str = "", name: str = "") -> None:
    """补发一个已过期的一次性任务。

    **必须等 bot 连上**：恢复发生在 `on_startup` 阶段，那时
    `nonebot.get_bots()` 还是空的（WebSocket 反向连接尚未建立），
    立刻执行只会得到「没有可用的 bot，跳过」——任务照样丢。

    这里轮询等待，最多 `_WAIT_BOT_TIMEOUT` 秒；超时才放弃。
    只有**执行成功**才删库里的记录：失败（比如等不到 bot）保留它，
    下次启动还有机会补上；成功还留着就是每次重启重放一遍。
    """
    import asyncio

    from ATRI.log import log

    waited = 0.0
    while waited < _WAIT_BOT_TIMEOUT:
        try:
            import nonebot

            if nonebot.get_bots():
                break
        except Exception:
            pass
        await asyncio.sleep(_WAIT_BOT_INTERVAL)
        waited += _WAIT_BOT_INTERVAL

    try:
        await func(**kwargs)
    except Exception as exc:
        log.warning(f"补发过期任务失败：{exc}")
        return
    if service and name:
        _forget_job(service, name)


#: 补发前等待 bot 上线的总时长（秒）。启动时 WebSocket 反连通常几秒内完成，
#: 给 60 秒足够覆盖网络慢的情况，又不会让任务无限期挂着。
_WAIT_BOT_TIMEOUT = 60.0

#: 轮询间隔（秒）
_WAIT_BOT_INTERVAL = 2.0


def _is_past(run_date: str) -> bool:
    """判断 date 触发器的 ISO 时间是否已经过去。"""
    import time
    from datetime import datetime

    try:
        return datetime.fromisoformat(run_date).timestamp() <= time.time()
    except (TypeError, ValueError):
        return False


ATRIScheduler: SchedulerController = SchedulerController(service="ATRI")
"""ATRI系统计划控制器"""
