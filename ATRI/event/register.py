from ATRI.log import log
from ATRI.scheduler import ATRIScheduler

from .data_source import ATRIEventBus

def daily_update():
    """每日更新触发"""
    log.info("开始每日更新")
    ATRIEventBus.publish('daily_update', 'ATRI')
    log.success("每日更新完成")

def register_triggers():
    """注册ATRI事件总线触发器"""
    ATRIScheduler.add_job(daily_update, 'daily_update', 'corn', hour=0, minute=0)
