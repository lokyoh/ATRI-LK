from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.adapters.onebot.v11.helpers import Cooldown
from nonebot.adapters.qq import MessageEvent as QQMessageEvent
from nonebot.params import CommandArg

from ATRI.service import Service
from ATRI.system.agent.agent.history import extract_plain_text, log_bot_message
from ATRI.system.lkapi.bot.checker import IsLkUser

from .data_source import feed_func

plugin = Service(
    "投喂", "向可爱的亚托莉投喂食物", "0.6.0", Service.ServiceType.ENTERTAINMENT
)

feed = plugin.on_command("投喂", "向可爱的亚托莉投喂食物", aliases={"投食", "投喂食物"})


@feed.handle([IsLkUser, Cooldown(600, prompt="稍后再投喂吧")])
async def _(event: MessageEvent | QQMessageEvent, args: Message = CommandArg()):
    user_id = event.get_user_id()
    food = args.extract_plain_text()
    reply = await feed_func(user_id, food)
    await feed.send(reply, at_sender=True)
    log_bot_message(getattr(event, "group_id", None), extract_plain_text(reply))
