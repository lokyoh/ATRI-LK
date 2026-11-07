import json
import time
import uuid

from ATRI.log import log
from ATRI.scheduler import ATRIScheduler

from .initiative_start import clean_opening_text, has_dialog_model, run_agent_once

#: 单次任务的最小提前量（秒）。太短会导致「刚设定就到点」，
#: 而调度器本身有几十毫秒的启动开销。
MIN_DELAY = 5.0

#: 单个会话最多允许挂多少个未完成任务，防止一次定一堆把调度器塞满。
MAX_TASKS_PER_CHAT = 10

#: 任务名前缀。核心调度器按 `<service>:<name>` 入库，
#: 这里统一加前缀，避免与将来其他使用者的任务重名。
_JOB_PREFIX = "agent:"


def _now() -> float:
    return time.time()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


async def _load_rows() -> list:
    """读出库里所有 **agent** 的任务行。"""
    try:
        from ATRI.scheduler.model import SchedulerTask

        return await SchedulerTask.filter(service="ATRI").all()
    except Exception as exc:
        log.debug(f"读取定时任务失败：{exc}")
        return []


def _row_to_task(row) -> dict:
    try:
        spec = json.loads(row.trigger_args or "{}")
    except (TypeError, ValueError):
        spec = {}
    try:
        payload = json.loads(row.job_kwargs or "{}")
    except (TypeError, ValueError):
        payload = {}
    task = payload.get("task") if isinstance(payload, dict) else None
    if not isinstance(task, dict):
        task = {}
    task.setdefault("id", row.name.removeprefix(_JOB_PREFIX))
    task.setdefault("name", row.name)
    task.setdefault("kind", row.trigger)
    task.setdefault("spec", spec)
    task.setdefault("run_at", 0)
    task.setdefault("chat_id", "")
    task.setdefault("message", "")
    task.setdefault("created_at", float(row.created_at or 0))
    task.setdefault("bot_id", "")
    return task


async def list_tasks(chat_id: str | None = None) -> list[dict]:
    """列出任务。给了 chat_id 就只列这个会话的。

    **归属判断看 `row.name`（数据库列），不看 task 字典里的 `name`**：
    后者是给用户看的描述（「提醒我喝水」），与「哪个服务的任务」无关。
    之前用 `task["name"].startswith("agent:")` 过滤，导致所有任务都被丢弃——
    `create_once` 构造的 task 里 `name` 就是用户内容，永远不以 `agent:` 开头。
    """
    tasks = []
    for row in await _load_rows():
        if not str(getattr(row, "name", "")).startswith(_JOB_PREFIX):
            continue
        tasks.append(_row_to_task(row))
    if chat_id is not None:
        tasks = [t for t in tasks if t["chat_id"] == str(chat_id)]
    return sorted(tasks, key=lambda t: t["created_at"])


async def get_task(task_id: str) -> dict | None:
    for t in await list_tasks():
        if t["id"] == task_id:
            return t
    return None


async def count_tasks(chat_id: str) -> int:
    return len(await list_tasks(chat_id))


async def _register(task: dict) -> bool:
    """把任务注册到核心调度器，并**可靠地**写进库。

    用 `add_job_async`：它会 await 持久化步骤。
    同步版 `add_job(persist=True)` 把写库丢到后台任务，在 AnyIO worker
    线程里 `get_event_loop()` 直接抛错、协程从未执行，结果是
    「调度器里有、库里没有」，重启后恢复不出来。

    写库失败时会撤回已注册的作业并抛错——宁可创建失败，
    也不要给用户一个「看起来成功、重启后就没了」的任务。
    """
    name = f"{_JOB_PREFIX}{task['id']}"
    try:
        if task["kind"] == "once":
            from datetime import datetime

            from apscheduler.triggers.date import DateTrigger

            run_at = float(task["run_at"] or 0)
            if run_at - _now() <= 0:
                log.debug(f"任务 {task['name']} 已过期，跳过注册")
                return False
            await ATRIScheduler.add_job_async(
                _send,
                name,
                DateTrigger(run_date=datetime.fromtimestamp(run_at)),
                kwargs={"task": task},
                misfire_grace_time=300,
                persist=True,
            )
            return True
        await ATRIScheduler.add_job_async(
            _send,
            name,
            task["kind"],
            kwargs={"task": task},
            misfire_grace_time=300,
            persist=True,
            **task["spec"],
        )
        return True
    except Exception as exc:
        log.warning(f"注册定时任务 {task.get('name')} 失败：{exc}")
        return False


def _unregister(task_id: str) -> None:
    """从核心调度器撤下作业；失败不抛（可能尚未注册）。"""
    name = f"{_JOB_PREFIX}{task_id}"
    try:
        if ATRIScheduler.has_job(name):
            ATRIScheduler.remove_job(name)
    except Exception as exc:
        log.debug(f"撤下定时任务失败：{exc}")


#: 等 bot 上线的上限（秒）。机器人刚启动时 WebSocket 反向连接还没建立，
#: `get_bots()` 是空的——此时直接发只会得到「没有可用的 bot」。
#: 任务是真的到点了，等几秒比丢掉强。
WAIT_BOT_TIMEOUT = 30.0

#: 轮询间隔（秒）
_WAIT_BOT_INTERVAL = 1.5


def _usable_bots() -> list:
    """当前已连接、能发群消息的 bot（OneBot 与 QQ 官方都收）。"""
    import nonebot

    from .sender import bot_mtype

    bots = []
    try:
        connected = list(nonebot.get_bots().values())
    except Exception:
        return []
    for b in connected:
        try:
            from nonebot.adapters.onebot.v11 import Bot as OneBot

            if isinstance(b, OneBot):
                bots.append(b)
                continue
        except Exception:
            pass
        if bot_mtype(b) == 1:
            bots.append(b)
    return bots


async def _wait_for_bots() -> list:
    """等到至少有一个可用的 bot，最多等 `WAIT_BOT_TIMEOUT` 秒。

    定时任务常在启动阶段被恢复，那时 bot 还没连上。
    之前的实现直接 `if not bots: return`，任务就此丢掉——
    用户看到的是「到点没反应」，日志里只有一行 warning。
    """
    import asyncio

    bots = _usable_bots()
    if bots:
        return bots
    waited = 0.0
    while waited < WAIT_BOT_TIMEOUT:
        await asyncio.sleep(_WAIT_BOT_INTERVAL)
        waited += _WAIT_BOT_INTERVAL
        bots = _usable_bots()
        if bots:
            log.info(f"定时任务等待 {waited:.1f} 秒后 bot 上线")
            return bots
    return []


async def _send(task: dict) -> None:
    """到点执行：**唤起 Agent 思考**，由她自己决定说什么。

    为什么不是直接发一句固定文本：定时任务的价值在于「到点提醒她该做这件事」，
    而不是「到点念一句录音」。让她重新思考一次，能结合当下的心情、日程、
    刚发生的事来说——「该提醒主人喝水了」和「主人你三小时没喝水了诶」是两种东西。

    真正发送由 `BotChatSender` 完成：思考链路里的 `send_message` 会经它落到群里，
    并自动写入聊天历史（与正常对话同一条路径）。
    """
    chat_id = str(task.get("chat_id") or "")
    cue = str(task.get("message") or "")
    if not chat_id or not cue:
        return

    bots = await _wait_for_bots()
    if not bots:
        log.warning(
            f"定时任务 {task.get('name')} 到点但等了 {WAIT_BOT_TIMEOUT:.0f} 秒"
            "仍没有可用的 bot，放弃本次"
        )
        return
    wanted = str(task.get("bot_id") or "")
    bot = None
    if wanted:
        for b in bots:
            if str(getattr(b, "self_id", "")) == wanted:
                bot = b
                break
        if bot is None:
            log.warning(
                f"定时任务「{task.get('name')}」原定 bot {wanted} 不在线，"
                f"改用 {getattr(bots[0], 'self_id', '?')} 发送"
            )
    if bot is None:
        bot = bots[0]
    try:
        await _run_agent(
            chat_id,
            bot,
            cue,
            task.get("name") or "",
            user_id=str(task.get("user_id") or ""),
        )
    except Exception as exc:
        log.warning(f"定时任务「{task.get('name')}」唤起 Agent 失败：{exc}")
        return
    log.info(f"定时任务「{task.get('name')}」已唤起 Agent（会话 {chat_id}）")
    if task.get("kind") == "once":
        _unregister(task["id"])


async def _run_agent(
    chat_id: str, bot, cue: str, name: str = "", user_id: str = ""
) -> None:
    """用一次完整的思考来回应定时任务的提醒。

    :param user_id: 委托这条提醒的人。传进去之后，提示词里会带上
        「和这个人的关系 / 关于他的记忆 / 用户画像」——
        定时任务本来就是对某个人说的，不该当成「没有目标用户」。
    """
    if not has_dialog_model():
        log.debug("没有可用的对话模型，定时任务跳过唤起 Agent")
        return

    from .sender import BotChatSender

    trigger = f"（定时提醒）{cue}"
    sender = BotChatSender(bot, chat_id, cleaner=clean_opening_text)
    await run_agent_once(
        chat_id, bot, trigger, sender, mode="scheduled", user_id=user_id
    )
    if sender.sent_count:
        from .history import log_bot_message
        from .initiative_start import readable_history_text

        for text in sender.sent:
            plain = readable_history_text(text)
            if plain:
                log_bot_message(chat_id, plain)
        for md_file, _ in sender.md_refs:
            log_bot_message(chat_id, f"[md:{md_file}]")


async def create_once(
    chat_id: str,
    message: str,
    delay_seconds: float,
    name: str = "",
    bot_id: str = "",
    user_id: str = "",
) -> str:
    """创建一个「N 秒后说这句话」的任务。返回任务 id。

    :param user_id: 委托这条提醒的人。到点时把它当作对话人，
        这样她能带着「和这个人的关系、关于他的记忆」来说话，
        而不是在一个没有对象的空上下文里硬找话。
    :raises ValueError: 参数不合法或超出数量上限。
    """
    if delay_seconds < MIN_DELAY:
        raise ValueError(f"至少要 {MIN_DELAY:.0f} 秒之后")
    if not (message or "").strip():
        raise ValueError("要说的内容不能为空")
    if await count_tasks(chat_id) >= MAX_TASKS_PER_CHAT:
        raise ValueError(f"这个会话已经有 {MAX_TASKS_PER_CHAT} 个任务了，先取消一些")

    task = {
        "id": _new_id(),
        "name": (name or message)[:30],
        "kind": "once",
        "spec": {},
        "run_at": _now() + float(delay_seconds),
        "chat_id": str(chat_id),
        "message": message.strip(),
        "created_at": _now(),
        "bot_id": str(bot_id or ""),
        "user_id": str(user_id or ""),
    }
    if not await _register(task):
        raise ValueError("任务注册失败，请稍后再试")
    return task["id"]


async def create_recurring(
    chat_id: str,
    message: str,
    *,
    kind: str,
    spec: dict,
    name: str = "",
    bot_id: str = "",
    user_id: str = "",
) -> str:
    """创建一个周期性任务（每天/每隔多久说固定的话）。

    :param kind: ``cron`` 或 ``interval``
    :param spec: 触发器参数，如 ``{"hour": 8, "minute": 0}``
    """
    if kind not in ("cron", "interval"):
        raise ValueError("kind 只能是 cron 或 interval")
    if not (message or "").strip():
        raise ValueError("要说的内容不能为空")
    if not spec:
        raise ValueError("缺少触发时间参数")
    if await count_tasks(chat_id) >= MAX_TASKS_PER_CHAT:
        raise ValueError(f"这个会话已经有 {MAX_TASKS_PER_CHAT} 个任务了，先取消一些")

    task = {
        "id": _new_id(),
        "name": (name or message)[:30],
        "kind": kind,
        "spec": spec,
        "run_at": 0,
        "chat_id": str(chat_id),
        "message": message.strip(),
        "created_at": _now(),
        "bot_id": str(bot_id or ""),
        "user_id": str(user_id or ""),
    }
    if not await _register(task):
        raise ValueError("任务注册失败，请检查时间参数是否合法")
    return task["id"]


async def cancel(task_id: str, chat_id: str | None = None) -> bool:
    """取消任务。给了 chat_id 时只允许取消该会话自己的任务。"""
    task = await get_task(task_id)
    if task is None:
        return False
    if chat_id is not None and task["chat_id"] != str(chat_id):
        return False
    _unregister(task_id)
    return True


def describe(task: dict) -> str:
    """把任务渲染成一句人能看懂的话，供模型回复用户。"""
    kind = task.get("kind")
    if kind == "once":
        left = max(0, int(float(task.get("run_at") or 0) - _now()))
        if left >= 3600:
            when = f"{left // 3600} 小时后"
        elif left >= 60:
            when = f"{left // 60} 分钟后"
        else:
            when = f"{left} 秒后"
    elif kind == "cron":
        spec = task.get("spec") or {}
        hour = spec.get("hour")
        minute = spec.get("minute", 0)
        if hour is not None:
            try:
                when = f"每天 {int(hour):02d}:{int(minute):02d}"
            except (TypeError, ValueError):
                when = "按 cron 规则"
        else:
            when = "按 cron 规则"
    else:
        spec = task.get("spec") or {}
        try:
            secs = (
                float(spec.get("seconds") or 0)
                or float(spec.get("minutes") or 0) * 60
                or float(spec.get("hours") or 0) * 3600
            )
        except (TypeError, ValueError):
            secs = 0
        when = f"每 {int(secs)} 秒" if secs else "按间隔规则"
    return f"[{task['id']}] {when}「{task.get('message', '')}」"


async def restore_agent_tasks() -> int:
    """启动时把库里属于 agent 的任务重新注册。返回成功数量。

    **恢复动作交给核心 `ATRIScheduler.restore()`**：任务定义是它写进库的，
    读回来也该由它负责。这里只做两件 agent 侧的事：

    1. 提供「任务名 → 可调用对象」映射（函数对象无法序列化，只能运行时给）；
    2. 任务名统一带 `_JOB_PREFIX`，避免与其他服务重名。

    之前这里自己遍历库、自己 `_register`，与核心调度器的持久化逻辑重复，
    而且 `load_persisted()` 从未被调用——「会存不会读」。
    """
    try:
        from ATRI.scheduler import restore
    except Exception as exc:
        log.warning(f"核心调度器不可用，跳过任务恢复：{exc}")
        return 0
    funcs: dict[str, object] = {}
    for task in await list_tasks():
        funcs[f"{_JOB_PREFIX}{task['id']}"] = _send
    if not funcs:
        return 0
    restored = await restore(funcs)
    if restored:
        log.info(f"已恢复 {restored} 个 Agent 定时任务")
    return restored
