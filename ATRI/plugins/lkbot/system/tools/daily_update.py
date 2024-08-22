from ATRI.utils.event import Event
from ATRI.utils.apscheduler import scheduler
from ATRI.log import log

daily_update_event = Event()


def daily_update():
    log.info("开始每日更新")
    daily_update_event.notify()
    log.success("每日更新完成")


scheduler.add_job(daily_update, 'cron', hour=0, minute=0)
