import asyncio
import inspect
import logging
from typing import ClassVar

from apscheduler.job import Job
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.date import DateTrigger
from nonebot.log import LoguruHandler

from ATRI.exceptions import BotRuntimeError, str_traceback

scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

aps_logger = logging.getLogger("apscheduler")
aps_logger.setLevel(30)
aps_logger.handlers.clear()
aps_logger.addHandler(LoguruHandler())


class SchedulerJob:
    """计划任务对象"""

    def __init__(self, func, id: str, trigger: str | BaseTrigger = "date", **kwargs):
        self.id = id
        self.job: Job = scheduler.add_job(
            func=func, trigger=trigger, id=id, name=id, **kwargs
        )

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
        **job_kwargs,
    ) -> SchedulerJob:
        """添加计划任务，使用方法同 apscheduler，并支持传递参数给任务函数"""
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
            **job_kwargs,
        )
        self.service_schedulers[self.service][name] = job
        job_ref["job"] = job
        return job

    def add_pause_job(
        self, func, name: str, trigger: str | BaseTrigger = "date", **kwargs
    ) -> SchedulerJob:
        """添加暂停的计划任务"""
        job = self.add_job(self, func, name, trigger=trigger, kwargs=kwargs)
        job.job.pause()
        return job

    def get_job(self, name) -> SchedulerJob:
        """获取SchedulerJob对象"""
        if not self.has_job(name):
            raise BotRuntimeError(f"找不到服务`{self.service}`的任务`{name}`")
        return self.service_schedulers[self.service][name]

    def remove_job(self, name):
        """移除指定Job"""
        if not self.has_job(name):
            raise BotRuntimeError(f"找不到服务`{self.service}`的任务`{name}`")
        job = self.get_job(name)
        if scheduler.get_job(job_id=job.id) is not None:
            job.job.remove()
        del self.service_schedulers[self.service][name]

    def has_job(self, name):
        """判断是否存在指定Job"""
        return (
            self.service in self.service_schedulers
            and name in self.service_schedulers[self.service]
        )

    @classmethod
    def get_job_by_id(cls, job_id: str) -> Job:
        return scheduler.get_job(job_id=job_id)


ATRIScheduler: SchedulerController = SchedulerController(service="ATRI")
"""ATRI系统计划控制器"""
