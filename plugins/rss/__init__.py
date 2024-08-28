from pathlib import Path

from nonebot.adapters.onebot.v11 import MessageEvent

from ATRI.permission import ADMIN
from ATRI.service import Service
from ATRI.message import MessageBuilder

RSS_PLUGIN_DIR = Path(".") / "ATRI" / "plugins" / "rss"

rss_helper = Service("rss").document("Rss系插件助手").type(Service.ServiceType.ADMIN).only_admin(True).permission(ADMIN)

rss_menu = rss_helper.on_command("/rss", "Rss帮助菜单")


@rss_menu.handle()
async def _rss_menu(event: MessageEvent):
    raw_rss_list = RSS_PLUGIN_DIR.glob("rss_*")
    rss_list = [str(i).split("\\")[-1] for i in raw_rss_list]

    result = (
        MessageBuilder("Rss Helper:")
        .text(
            f"可用订阅源: {', '.join(map(str, rss_list)).replace('rss_', str())}".replace(
                "mikanan", "mikan"
            )
        )
        .text("详细请: /帮助 rss.(订阅源)")
    )
    await rss_menu.finish(result)
