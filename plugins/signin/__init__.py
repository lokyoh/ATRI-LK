from random import choice

from nonebot.adapters.onebot.v11.event import MessageEvent
from nonebot.adapters.onebot.v11.helpers import Cooldown
from nonebot.internal.matcher import Matcher

from ATRI.service import Service
from ATRI.system.lkapi.bot.checker import IsLkUser

from .data_source import signin
from . import core_signin

plugin = Service(
    "签到",
    "亚托莉的签到系统",
    "0.2.1",
    Service.ServiceType.ENTERTAINMENT
)

_lmt_notice = ["慢...慢一..点❤", "冷静1下", "歇会歇会~~", "呜呜...别急", "太快了...受不了", "不要这么快呀"]

sign_in = plugin.on_command(cmd='签到', docs="亚托莉的签到系统", aliases={"今日签到", "每日签到"})


@sign_in.handle([IsLkUser, Cooldown(60, prompt=choice(_lmt_notice))])
async def _(event: MessageEvent, matcher: Matcher):
    await signin.signin(event, matcher)
