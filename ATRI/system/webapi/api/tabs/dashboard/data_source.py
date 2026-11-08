import asyncio
import time
from datetime import datetime, timedelta

import nonebot
from nonebot.adapters import Bot
from nonebot.adapters.onebot.v11 import Bot as OneBot
from nonebot.adapters.qq import Bot as QQBot
from nonebot.adapters.qq.models import User as QQUser
from nonebot.drivers import Driver
from tortoise.expressions import RawSQL
from tortoise.functions import Count

from ATRI.bot.statistics import MessageStatistics, ServiceStatistics
from ATRI.bot.utils import BotUtils
from ATRI.exceptions import str_traceback
from ATRI.log import log

from ..main.data_source import bot_live
from . import cache
from .model import (
    AllChatAndCallCount,
    BotInfo,
    ChatCallMonthCount,
    QueryChatCallCount,
)

driver: Driver = nonebot.get_driver()


CONNECT_TIME = 0


@driver.on_startup
async def _():
    global CONNECT_TIME
    CONNECT_TIME = int(time.time())


@driver.on_bot_connect
async def _(bot: Bot):
    """重连时丢掉该 bot 的身份缓存。

    重连可能意味着换了账号、改了昵称，继续用旧缓存会一直显示过期信息。
    只清这一台，不影响别的机器人。
    """
    cache.clear(bot.self_id)


class ApiDataSource:
    @classmethod
    async def __fetch_identity(cls, bot: Bot) -> cache.Identity:
        """真正去打远端拿身份信息——慢的那部分，只应该偶尔执行。"""
        platform = "Unknow"
        nickname = bot.self_id
        ava_url = ""
        if isinstance(bot, OneBot):
            platform = "onebot11"
            login_info = await bot.get_login_info()
            nickname = login_info["nickname"]
            ava_url = BotUtils.get_user_avatar_url(bot.self_id, platform) or ""
        if isinstance(bot, QQBot):
            platform = "qq"
            try:
                login_info: QQUser = await bot.me()
                nickname = login_info.username
                ava_url = login_info.avatar if login_info.avatar else ""
            except Exception:
                log.warning("QQ获取信息失败")

        identity = cache.Identity(
            self_id=bot.self_id,
            nickname=nickname,
            ava_url=ava_url,
            platform=platform,
        )
        try:
            group, friend = await asyncio.gather(
                BotUtils.get_group_list(bot),
                BotUtils.get_friend_list(bot),
            )
            identity.group_count = len(group)
            identity.friend_count = len(friend)
        except Exception as e:
            log.warning(f"获取bot好友/群组信息失败...:{str_traceback(e)}")
        return identity

    @classmethod
    async def __identity(cls, bot: Bot) -> cache.Identity:
        """取身份信息：命中缓存就直接用，否则拉一次并写入。

        这是「QQ 端加载慢」的根治点——``bot.me()`` 每次刷新都打一遍 QQ 开放
        平台，慢且毫无必要；昵称、头像、群数这些一天都不会变。
        """
        cached = cache.get(bot.self_id)
        if cached is not None:
            return cached
        identity = await cls.__fetch_identity(bot)
        cache.put(identity)
        return identity

    @classmethod
    async def __build_bot_info(cls, bot: Bot) -> BotInfo:
        """构建Bot信息

        **身份**部分走缓存（慢、几乎不变），**统计**部分每次实查（快、要最新）。

        参数:
            bot: Bot

        返回:
            BotInfo: Bot信息
        """
        now = datetime.now()
        identity = await cls.__identity(bot)
        bot_info = BotInfo(
            self_id=identity.self_id,
            nickname=identity.nickname,
            ava_url=identity.ava_url,
            platform=identity.platform,
            group_count=identity.group_count,
            friend_count=identity.friend_count,
        )
        bot_info.day_call = await ServiceStatistics.filter(
            created_at__gte=now - timedelta(hours=now.hour, minutes=now.minute),
            bot_id=bot.self_id,
        ).count()
        bot_info.received_messages = await MessageStatistics.filter(
            bot_id=bot_info.self_id,
            created_at__gte=now - timedelta(hours=now.hour, minutes=now.minute),
        ).count()
        bot_info.connect_time = bot_live.get(bot.self_id) or 0
        if bot_info.connect_time:
            connect_date = datetime.fromtimestamp(CONNECT_TIME)
            bot_info.connect_date = connect_date.strftime("%Y-%m-%d %H:%M:%S")
        return bot_info

    @classmethod
    async def get_bot_list(cls) -> list[BotInfo]:
        """获取bot列表

        返回:
            list[BotInfo]: Bot列表
        """
        bots = list(nonebot.get_bots().values())
        if not bots:
            return []
        # 多机器人并发构建：串行时总耗时是各家的和，并发后只等最慢的那台。
        # 缓存命中后这里本来就很快，未命中时（尤其 QQ 的 bot.me()）差别明显。
        return list(await asyncio.gather(*(cls.__build_bot_info(bot) for bot in bots)))

    @classmethod
    async def get_chat_and_call_count(cls, bot_id: str | None) -> QueryChatCallCount:
        """获取今日聊天和调用次数

        参数:
            bot_id: bot id

        返回:
            QueryChatCallCount: 数据内容
        """
        now = datetime.now()
        query = MessageStatistics
        if bot_id:
            query = query.filter(bot_id=bot_id)
        chat_all_count = await query.annotate().count()
        chat_day_count = await query.filter(
            created_at__gte=now - timedelta(hours=now.hour, minutes=now.minute)
        ).count()
        query = ServiceStatistics
        if bot_id:
            query = query.filter(bot_id=bot_id)
        call_all_count = await query.annotate().count()
        call_day_count = await query.filter(
            created_at__gte=now - timedelta(hours=now.hour, minutes=now.minute)
        ).count()
        return QueryChatCallCount(
            chat_num=chat_all_count,
            chat_day=chat_day_count,
            call_num=call_all_count,
            call_day=call_day_count,
        )

    @classmethod
    async def get_all_chat_and_call_count(
        cls, bot_id: str | None
    ) -> AllChatAndCallCount:
        """获取全部聊天和调用记录

        参数:
            bot_id: bot id

        返回:
            AllChatAndCallCount: 数据内容
        """
        now = datetime.now()
        query = MessageStatistics
        if bot_id:
            query = query.filter(bot_id=bot_id)
        chat_week_count = await query.filter(
            created_at__gte=now - timedelta(days=7, hours=now.hour, minutes=now.minute)
        ).count()
        chat_month_count = await query.filter(
            created_at__gte=now - timedelta(days=30, hours=now.hour, minutes=now.minute)
        ).count()
        chat_year_count = await query.filter(
            created_at__gte=now
            - timedelta(days=365, hours=now.hour, minutes=now.minute)
        ).count()
        query = ServiceStatistics
        if bot_id:
            query = query.filter(bot_id=bot_id)
        call_week_count = await query.filter(
            created_at__gte=now - timedelta(days=7, hours=now.hour, minutes=now.minute)
        ).count()
        call_month_count = await query.filter(
            created_at__gte=now - timedelta(days=30, hours=now.hour, minutes=now.minute)
        ).count()
        call_year_count = await query.filter(
            created_at__gte=now
            - timedelta(days=365, hours=now.hour, minutes=now.minute)
        ).count()
        return AllChatAndCallCount(
            chat_week=chat_week_count,
            chat_month=chat_month_count,
            chat_year=chat_year_count,
            call_week=call_week_count,
            call_month=call_month_count,
            call_year=call_year_count,
        )

    @classmethod
    async def get_chat_and_call_month(cls, bot_id: str | None) -> ChatCallMonthCount:
        """获取一个月内的调用/消息记录次数，并根据日期对数据填充0

        参数:
            bot_id: bot id

        返回:
            ChatCallMonthCount: 数据内容
        """
        now = datetime.now()
        filter_date = now - timedelta(days=30, hours=now.hour, minutes=now.minute)
        chat_query = MessageStatistics
        call_query = ServiceStatistics
        if bot_id:
            chat_query = chat_query.filter(bot_id=bot_id)
            call_query = call_query.filter(bot_id=bot_id)
        chat_date_list = (
            await chat_query.filter(created_at__gte=filter_date)
            .annotate(date=RawSQL("DATE(created_at)"), count=Count("id"))
            .group_by("date")
            .values("date", "count")
        )
        call_date_list = (
            await call_query.filter(created_at__gte=filter_date)
            .annotate(date=RawSQL("DATE(created_at)"), count=Count("id"))
            .group_by("date")
            .values("date", "count")
        )
        chat_date2cnt = {str(date["date"]): date["count"] for date in chat_date_list}
        call_date2cnt = {str(date["date"]): date["count"] for date in call_date_list}
        date = now.date()
        chat_count_list = []
        call_count_list = []
        date_list = []
        for _ in range(30):
            if str(date) in chat_date2cnt:
                chat_count_list.append(chat_date2cnt[str(date)])
            else:
                chat_count_list.append(0)
            if str(date) in call_date2cnt:
                call_count_list.append(call_date2cnt[str(date)])
            else:
                call_count_list.append(0)
            date_list.append(str(date)[5:])
            date -= timedelta(days=1)
        chat_count_list.reverse()
        call_count_list.reverse()
        date_list.reverse()
        return ChatCallMonthCount(
            chat=chat_count_list, call=call_count_list, date=date_list
        )
