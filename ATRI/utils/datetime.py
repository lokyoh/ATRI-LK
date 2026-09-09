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

def now(tz_name: str | None = None) -> datetime.datetime:
    if tz_name:
        return datetime.datetime.now(ZoneInfo(tz_name))
    return datetime.datetime.now(TIMEZONE)

def today(tz_name: str | None = None) -> datetime.date:
    return now(tz_name).date()

def fromtimestamp(fromtimestamp: float, tz_name: str | None = None) -> datetime.datetime:
    if tz_name:
        return datetime.datetime.fromtimestamp(fromtimestamp, ZoneInfo(tz_name))
    return datetime.datetime.fromtimestamp(fromtimestamp, TIMEZONE)

def date_fromtimestamp(timestamp: float, tz_name: str | None = None) -> datetime.date:
    return fromtimestamp(timestamp, tz_name).date()

def now_timestamp(tz_name: str | None = None) -> float:
    return now(tz_name).timestamp()

def now_time(tz_name: str | None = None) -> datetime.time:
    return now(tz_name).time()
