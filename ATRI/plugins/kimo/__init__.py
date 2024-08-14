from random import choice

from nonebot.matcher import Matcher
from nonebot.params import ArgPlainText, CommandArg
from nonebot.adapters.onebot.v11 import MessageEvent, Message
from nonebot.adapters.onebot.v11.helpers import Cooldown

from ATRI.service import Service
from ATRI.rule import to_bot
from ATRI.utils.apscheduler import scheduler

from .data_source import Kimo

plugin = Service("kimo").document("好像有点涩?").type(Service.ServiceType.ENTERTAINMENT).rule(to_bot()).priority(996)

_chat_flmt_notice = ["慢...慢一..点❤", "冷静1下", "歇会歇会~~", "我开始为你以后的伴侣担心了..."]

kimo = plugin.on_message("文爱", "闲聊（文爱", priority=996, block=False)


@kimo.handle([Cooldown(3, prompt=choice(_chat_flmt_notice))])
async def _chat(event: MessageEvent):
    user_id = event.get_user_id()
    msg = str(event.message)
    repo = await Kimo().deal(msg, user_id)
    try:
        if repo:
            await kimo.send(repo)
    except Exception:
        return


kimo_c = plugin.on_command("文爱", "闲聊（文爱")


@kimo_c.handle([Cooldown(3, prompt=choice(_chat_flmt_notice))])
async def _chat(event: MessageEvent, args: Message = CommandArg()):
    user_id = event.get_user_id()
    msg = args.extract_plain_text()
    repo = await Kimo().deal(msg, user_id)
    try:
        if repo:
            await kimo_c.send(repo)
    except Exception:
        return


my_name_is = plugin.on_command("叫我", "更改kimo时的称呼", aliases={"我是"}, priority=1)


@my_name_is.handle([Cooldown(3, prompt=choice(_chat_flmt_notice))])
async def _name(matcher: Matcher, args: Message = CommandArg()):
    msg = args.extract_plain_text()
    if msg:
        matcher.set_arg("name", args)


@my_name_is.got("name", "欧尼酱想让咱如何称呼呢！0w0")
async def _deal_name(event: MessageEvent, new_name: str = ArgPlainText("name")):
    user_id = event.get_user_id()
    repo = choice(
        [
            f"好~w 那咱以后就称呼你为{new_name}！",
            f"噢噢噢！原来你叫{new_name}阿~",
            f"好欸！{new_name}ちゃん~~~",
            "很不错的称呼呢w",
        ]
    )
    Kimo().name_is(user_id, new_name)
    await my_name_is.finish(repo)


@scheduler.scheduled_job("interval", name="kimo词库检查更新", hours=3, misfire_grace_time=60)  # type: ignore
async def _check_kimo():
    try:
        await Kimo().update_data()
    except Exception:
        pass
