from typing_extensions import Type

from nonebot.internal.matcher import Matcher
from nonebot.adapters.onebot.v11 import Event, GroupMessageEvent, PrivateMessageEvent
from nonebot.adapters.onebot.v11.message import Message, MessageSegment

from .util import lk_util
from .config import config


async def is_lk_user(matcher: Type[Matcher], event: Event):
    user_id = event.get_user_id()
    if not lk_util.is_valid_user(user_id):
        if type(event) is GroupMessageEvent:
            msg = Message().append(MessageSegment.at(event.get_user_id())).append(MessageSegment.text(lk_util.bind_tip))
            await matcher.finish(msg)
        elif type(event) is PrivateMessageEvent:
            await matcher.finish(lk_util.bind_tip)


async def not_safe_mode(matcher: Type[Matcher], event: GroupMessageEvent):
    if lk_util.is_safe_mode_group(event.group_id):
        await matcher.finish(lk_util.safe_mode_tip)


async def is_test_mode(matcher: Type[Matcher], event: GroupMessageEvent):
    if not lk_util.is_test_group(event.group_id):
        await matcher.finish(lk_util.test_mode_tip)


async def is_chat_switch_on(matcher: Type[Matcher]):
    if not config.config.chat_switch:
        await matcher.finish(lk_util.chat_switch_off)
