import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TIMEZONE = ZoneInfo("Asia/Shanghai")


def set_timezone(tz_name: str):
    global TIMEZONE
    try:
        TIMEZONE = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        from ATRI import conf_m
        from ATRI.log import log

        log.warning(f"时区 '{tz_name}' 设置失败, 使用默认时区: Asia/Shanghai.")
        TIMEZONE = ZoneInfo("Asia/Shanghai")
        conf_m.config.ConfigModel.BotConfig.timezone = "Asia/Shanghai"
        conf_m.save_conf()
    _sync_scheduler_timezone()


def _sync_scheduler_timezone():
    """让调度器跟着全局时区走；cron 按哪个时区算必须与日期工具一致。"""
    try:
        from ATRI.scheduler import scheduler
    except Exception:
        return
    if getattr(scheduler, "running", False):
        # 已启动后再改时区会让已排好的下次执行时间错位，保持原样。
        return
    scheduler.timezone = TIMEZONE


def now(tz_name: str | None = None) -> datetime.datetime:
    if tz_name:
        return datetime.datetime.now(ZoneInfo(tz_name))
    return datetime.datetime.now(TIMEZONE)


def today(tz_name: str | None = None) -> datetime.date:
    return now(tz_name).date()


def fromtimestamp(
    fromtimestamp: float, tz_name: str | None = None
) -> datetime.datetime:
    if tz_name:
        return datetime.datetime.fromtimestamp(fromtimestamp, ZoneInfo(tz_name))
    return datetime.datetime.fromtimestamp(fromtimestamp, TIMEZONE)


def date_fromtimestamp(timestamp: float, tz_name: str | None = None) -> datetime.date:
    return fromtimestamp(timestamp, tz_name).date()


def now_timestamp(tz_name: str | None = None) -> float:
    return now(tz_name).timestamp()


def now_time(tz_name: str | None = None) -> datetime.time:
    return now(tz_name).time()
