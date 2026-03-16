from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.exception import IgnoredException
from nonebot.matcher import Matcher
from nonebot.message import run_preprocessor

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


def init_listener():
    """初始化监听器"""
