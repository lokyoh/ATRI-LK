from ATRI.utils.event import BaseEvents, BaseEvent
from ATRI.log import log

daily_update_event = BaseEvents()
"""每日数据更新事件"""


def daily_update():
    log.info("开始每日更新")
    daily_update_event.notify(BaseEvent("每日数据更新"))
    log.success("每日更新完成")
