from nonebot.adapters import Event as BaseEvent
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.internal.matcher import Matcher
from nonebot.internal.params import Depends

from .config import config
from .data_source import LKBot
from .util import lk_util


async def is_lk_user(matcher: Matcher, event: BaseEvent):
    """检查并自动绑定首次使用 LK 功能的用户。"""
    user_id = event.get_user_id()
    if not lk_util.is_valid_user(user_id):
        sender = getattr(event, "sender", None)
        author = getattr(event, "author", None)
        nickname = getattr(sender, "nickname", None) or getattr(
            author, "username", None
        )
        LKBot.auto_bind_user(user_id, nickname)


async def not_safe_mode(matcher: Matcher, event: GroupMessageEvent):
    """检查是否是健康模式的群聊，是则中断当前事件处理"""
    if lk_util.is_safe_mode_group(event.group_id):
        await matcher.finish(lk_util.safe_mode_tip)


async def is_test_mode(matcher: Matcher, event: GroupMessageEvent):
    """检查是否是测试模式的群聊，不是则中断当前事件处理"""
    if not lk_util.is_test_group(event.group_id):
        await matcher.finish(lk_util.test_mode_tip)


async def is_chat_switch_on(matcher: Matcher):
    """检查是否开启聊天，不是则中断当前事件处理"""
    if not config.get("chat_switch"):
        await matcher.finish(lk_util.chat_switch_off)


IsLkUser = Depends(is_lk_user)
"""检查是否是LK插件的用户，不是则中断当前事件处理"""
NotSafeMode = Depends(not_safe_mode)
"""检查是否是健康模式的群聊，是则中断当前事件处理"""
IsTestMode = Depends(is_test_mode)
"""检查是否是测试模式的群聊，不是则中断当前事件处理"""
IsChatSwitchOn = Depends(is_chat_switch_on)
"""检查是否开启聊天，不是则中断当前事件处理"""
