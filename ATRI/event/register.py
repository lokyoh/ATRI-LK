from ATRI import driver
from ATRI.log import log
from ATRI.scheduler import ATRIScheduler

from .data_source import ATRIEventBus


async def daily_update():
    """每日更新触发"""
    log.info("开始每日更新")
    await ATRIEventBus.publish("daily_update", "ATRI")
    log.success("每日更新完成")


class ATRIHeartbeat:
    heartbeat_count = 0

    @classmethod
    async def heartbeat(cls):
        await ATRIEventBus.publish("heartbeat_1m", "ATRI")
        cls.heartbeat_count += 1
        if cls.heartbeat_count == 30:
            await ATRIEventBus.publish("heartbeat_30m", "ATRI")
            cls.heartbeat_count = 0


def register_triggers():
    """注册ATRI事件总线触发器"""
    ATRIScheduler.add_job(daily_update, "daily_update", "cron", hour=0, minute=0)
    ATRIScheduler.add_job(
        ATRIHeartbeat.heartbeat,
        "heartbeat",
        "interval",
        minutes=1,
        max_instances=1,
        coalesce=True,
        use_log=False,
    )


@driver().on_shutdown
async def shutdown():
    await ATRIEventBus.publish("shutdown", "ATRI")
