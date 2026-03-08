from random import choice

from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
from nonebot.adapters.onebot.v11.helpers import Cooldown
from nonebot.matcher import Matcher
from nonebot.params import ArgPlainText, CommandArg

from ATRI.message import MessageBuilder
from ATRI.service import Service

_repo_flmt_notice = choice(["慢...慢一..点❤", "冷静1下", "歇会歇会~~"])

_REPO_FORMAT = MessageBuilder("来自用户{user}反馈:").text("{msg}").done()

plugin = Service("反馈", "向维护者发送消息", "1.1.0", Service.ServiceType.SYSTEM)

reporter = plugin.on_command("来杯红茶", "向维护者发送消息", aliases={"反馈", "报告"})


@reporter.handle([Cooldown(120, prompt=_repo_flmt_notice)])
async def _ready_repo(matcher: Matcher, args: Message = CommandArg()):
    msg = args.extract_plain_text()
    if msg:
        matcher.set_arg("repo", args)


@reporter.got("repo", "需要反馈的内容呢？~")
async def _deal_repo(
    bot: Bot,
    event: MessageEvent,
    repo_msg: str = ArgPlainText("repo"),
):
    user_id = event.get_user_id()
    repo_0 = _REPO_FORMAT.format(user=user_id, msg=repo_msg)

    try:
        await plugin.send_to_master(repo_0)
    except Exception:
        await reporter.finish("发送失败了呢...")

    await reporter.finish("吾辈的心愿已由咱转告维护者！")
