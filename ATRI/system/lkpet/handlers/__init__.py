"""Command and event handlers for the pet plugin."""

from .. import config, plugin
from ..core.notify import settle_and_notify
from . import events, pet  # noqa: F401

# 每日在设定时刻结算所有宠物的过期天数并推送「新的一天」通知。
# 时间取自插件设置（服务器时区），缺省 00:00。
plugin.scheduler_jobs().add_job(
    settle_and_notify,
    "宠物日结算通知",
    "cron",
    hour=config.get("settle_check_hour", 0),
    minute=config.get("settle_check_minute", 0),
)
