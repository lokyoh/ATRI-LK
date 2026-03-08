from pathlib import Path

from nonebot.adapters.onebot.v11 import MessageEvent

from ATRI.bot import BotUtils
from ATRI.exceptions import BaseBotException
from ATRI.message import MessageBuilder
from ATRI.permission import ADMIN
from ATRI.service import Service

RSS_PLUGIN_DIR = Path(".") / "plugins" / "rss"


class RssError(BaseBotException):
    prompt = "RSS订阅错误"


rss_helper = Service(
    "rss", "Rss系插件助手", "1.0.1", Service.ServiceType.SUBSCRIBE
).permission(ADMIN)

rss_menu = rss_helper.on_command("rss", "Rss帮助菜单")


@rss_menu.handle()
async def _rss_menu(event: MessageEvent):
    raw_rss_list = RSS_PLUGIN_DIR.glob("rss_*")
    rss_list = [str(i).split("\\")[-1] for i in raw_rss_list]

    result = (
        MessageBuilder("Rss Helper:")
        .text(f"可用订阅源: {', '.join(map(str, rss_list)).replace('rss_', str())}")
        .text(f"详细请: {BotUtils.get_command_start()}帮助 rss.(订阅源)")
    )
    await rss_menu.finish(result)
