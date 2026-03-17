import asyncio
import inspect
import logging
from typing import Dict

from apscheduler.job import Job
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.base import BaseTrigger
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

    service_schedulers: Dict[str, Dict[str, SchedulerJob]] = {}

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
            del job_kwargs["id"]

        def job_func(f):
            from ATRI.log import log

            if inspect.iscoroutinefunction(f):

                async def wrapper(*args, **kwargs):
                    try:
                        log.debug(f"开始执行`{self.service}`的任务`{name}`")
                        await f(*args, **kwargs)
                        log.debug(f"`{self.service}`的任务`{name}`执行完毕")
                    except Exception as e:
                        log.error(
                            f"在执行`{self.service}`的任务`{name}`时失败:\n{str_traceback(e)}"
                        )

                return wrapper
            else:

                async def wrapper(*args, **kwargs):
                    try:
                        log.debug(f"开始执行`{self.service}`的任务`{name}`")
                        await asyncio.to_thread(f, *args, **kwargs)
                        log.debug(f"`{self.service}`的任务`{name}`执行完毕")
                    except Exception as e:
                        log.error(
                            f"在执行`{self.service}`的任务`{name}`时失败:\n{str_traceback(e)}"
                        )

                return wrapper

        job = SchedulerJob(
            func=job_func(func),
            trigger=trigger,
            id=f"{self.service}-{name}",
            **job_kwargs,
        )
        self.service_schedulers[self.service][name] = job
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
        if (
            self.service not in self.service_schedulers
            or name not in self.service_schedulers[self.service]
        ):
            raise BotRuntimeError(f"找不到服务`{self.service}`的任务`{name}`")
        return self.service_schedulers[self.service][name]

    def remove_job(self, name):
        """移除指定Job"""
        if (
            self.service not in self.service_schedulers
            or name not in self.service_schedulers[self.service]
        ):
            raise BotRuntimeError(f"找不到服务`{self.service}`的任务`{name}`")
        self.get_job(name).job.remove()
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
