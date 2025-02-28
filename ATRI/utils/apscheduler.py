import logging

from apscheduler.job import Job
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.base import BaseTrigger

from nonebot.log import LoguruHandler

from ATRI.exceptions import BotRuntimeError

scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

aps_logger = logging.getLogger("apscheduler")
aps_logger.setLevel(30)
aps_logger.handlers.clear()
aps_logger.addHandler(LoguruHandler())


class SchedulerController:
    """服务的计划任务控制器"""
    service_schedulers = {}

    def __init__(self, service: str):
        self.service = service
        if service in self.service_schedulers:
            return
        self.service_schedulers[service] = {}

    def add_job(self, func, name: str, trigger: str | BaseTrigger = 'date', **kwargs):
        if name in self.service_schedulers[self.service]:
            raise BotRuntimeError(f'创建服务`{self.service}`的任务`{name}`失败：该任务名称已存在')

        def job_func(f):
            async def wrapper():
                try:
                    if (f.__code__.co_flags & 80) != 0:
                        await f()
                    else:
                        f()
                except Exception as e:
                    from ATRI.log import log
                    log.error(f'在执行`{self.service}`的任务`{name}`时失败:`{e}`')

            return wrapper

        self.service_schedulers[self.service][name] = scheduler.add_job(func=job_func(func), trigger=trigger,
                                                                        id=f'{self.service}-{name}', name=name,
                                                                        **kwargs)

    def get_job(self, name) -> Job:
        if self.service not in self.service_schedulers or name not in self.service_schedulers[self.service]:
            raise BotRuntimeError(f'找不到服务`{self.service}`的任务`{name}`')
        return self.service_schedulers[self.service][name]

    def remove_job(self, name):
        self.get_job(name).remove()
        del self.service_schedulers[self.service][name]
