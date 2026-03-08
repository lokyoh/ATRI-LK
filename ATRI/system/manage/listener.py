from nonebot.adapters.onebot.v11 import (
    Event,
    MessageEvent,
)
from nonebot.exception import IgnoredException
from nonebot.matcher import Matcher
from nonebot.message import run_preprocessor

from ATRI.bot import BotStatus, GlobalStatus
from ATRI.service import ServiceTools


@run_preprocessor
async def _(matcher: Matcher, event: MessageEvent):
    plugin_name = str(matcher.plugin_name)
    if "nonebot_" not in plugin_name:
        return
    serv = ServiceTools(plugin_name)
    try:
        serv.load_service_config()
    except Exception:
        raise IgnoredException(f"{plugin_name} limited")
    if not serv.auth_service():
        raise IgnoredException(f"{plugin_name} limited")
    user_id = str(getattr(event, "user_id", None))
    group_id = str(getattr(event, "group_id", None))
    result = serv.auth_service(user_id, group_id)
    if not result:
        raise IgnoredException(f"{plugin_name} limited")


@run_preprocessor
async def _(event: Event):
    bot_id = str(event.self_id)
    user_id = str(getattr(event, "user_id", ""))
    group_id = str(getattr(event, "group_id", ""))

    if GlobalStatus.is_blocked(user_id, group_id):
        raise IgnoredException(
            f"Blocked by GlobalStatus: user_id={user_id}, group_id={group_id}"
        )
    if BotStatus.is_blocked(bot_id, user_id, group_id):
        raise IgnoredException(
            f"Blocked by BotStatus: bot_id={bot_id}, user_id={user_id}, group_id={group_id}"
        )


def init_listener():
    """初始化监听器"""
