from typing import Optional

from nonebot.adapters.onebot.v11 import ActionFailed, GroupMessageEvent, Bot, Event, PokeNotifyEvent
from nonebot.matcher import Matcher
from nonebot.message import event_postprocessor, run_postprocessor

from ATRI.event import heartbeat_1m, shutdown
from ATRI.exceptions import EventRuntimeError, BaseBotException, save_error, str_traceback
from ATRI.log import log
from ATRI.message import MessageBuilder
from ATRI.utils import Limiter

from .model import MessageStatistics, ServiceStatistics

TEMP_LIST = []

limiter = Limiter(3, 600)


@event_postprocessor
async def add_message(bot: Bot, event: Event):
    if event.post_type != 'message' and not isinstance(event, PokeNotifyEvent):
        """过滤除poke外的notice"""
        return
    bot_id = bot.self_id
    user_id = str(event.user_id)
    group_id = None
    message_type = "private"
    if hasattr(event, 'group_id') and event.group_id:
        group_id = str(event.group_id)
        message_type = "group"
    await MessageStatistics.create(
        bot_id=bot_id,
        user_id=user_id,
        group_id=group_id,
        message_type=message_type,
    )


def add_server_statistic(bot: Bot, event: Event, matcher: Matcher, track_id: str | None = None):
    if matcher.module_name:
        service = matcher.module_name
        if service:
            bot_id = bot.self_id
            target_id = event.get_user_id()
            target_type = 'private'
            if hasattr(event, 'group_id') and event.group_id:
                target_id = event.group_id
                target_type = 'group'
            TEMP_LIST.append(ServiceStatistics(
                bot_id=bot_id,
                service_id=service,
                call_type=matcher.type,
                target_id=target_id,
                target_type=target_type,
                track_id=track_id,
            ))


@run_postprocessor
async def _(bot: Bot, event, matcher: Matcher, exception: Optional[Exception]):
    need_statistic = True
    if matcher.type == "notice" and not isinstance(event, PokeNotifyEvent):
        """过滤除poke外的notice"""
        need_statistic = False
    if not matcher.block:
        need_statistic = False
    if not exception:
        if need_statistic:
            add_server_statistic(bot, event, matcher)
        return
    if isinstance(exception, EventRuntimeError):
        exception: EventRuntimeError
        prompt = "事件运行错误 " + exception.prompt or exception.__class__.__name__
        track_id = save_error(prompt, exception.content)
        log.error(f"EventRuntimeError: {prompt}")
    elif isinstance(exception, BaseBotException):
        exception: BaseBotException
        prompt = "机器人基本错误 " + exception.prompt or exception.__class__.__name__
        track_id = save_error(prompt, str_traceback(exception))
        log.error(f"BotException: {prompt}")
    elif isinstance(exception, ActionFailed):
        prompt = "发送错误 请参考协议端输出"
        track_id = save_error(prompt, str_traceback(exception))
        log.error(f"ActionFailed: {prompt}")
    elif isinstance(exception, Exception):
        prompt = "其他错误 " + exception.__class__.__name__
        track_id = save_error(prompt, str_traceback(exception))
        log.error(f"Exception: {prompt}")
    else:
        prompt = "未知错误 " + exception.__class__.__name__
        track_id = save_error(prompt, str_traceback(exception))
        log.error(f"Ignore Exception: {prompt}")
    log.error(f"Error Track ID: {track_id}")
    msg = (
        MessageBuilder("呜——出错了...请反馈维护者")
        .text(f"来自: {matcher.module_name}")
        .text(f"信息: {prompt}")
        .text(f"追踪ID: {track_id}")
    )
    if need_statistic:
        add_server_statistic(bot, event, matcher, track_id)
    if isinstance(event, GroupMessageEvent):
        group_id = str(event.group_id)
        if not limiter.check(group_id):
            msg = MessageBuilder("该群报错提示已达限制, 将冷却10min").text("如需反馈请: 来杯红茶")
        else:
            limiter.increase(group_id)
        if limiter.get_times(group_id) > 3:
            return
    try:
        await bot.send(event, msg)
    except Exception:
        return


@heartbeat_1m()
@shutdown()
async def save_temp():
    try:
        call_list = TEMP_LIST.copy()
        TEMP_LIST.clear()
        if call_list:
            await ServiceStatistics.bulk_create(call_list)
            log.debug(f"批量添加调用记录 {len(call_list)} 条")
    except Exception as e:
        log.error(f"定时批量添加调用记录发生错误:{str_traceback(e)}")
