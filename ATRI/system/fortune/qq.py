from nonebot.adapters.qq.models import Action, Button, Permission, RenderData

from ATRI.bot.model import PanelItem
from ATRI.bot.utils import BotUtils
from ATRI.event import ATRIEventBus

cmd = BotUtils.get_command_start()
BTN_FORTUNE = Button(
    id="btn_fortune",
    render_data=RenderData(label="今日运势", style=1),
    action=Action(
        type=2,
        permission=Permission(type=2),
        data=f"{cmd}今日运势",
        enter=True,
    ),
)


@ATRIEventBus.subscribe("set_panel")
def get_fortune_menu_item(event):
    return PanelItem(
        name=f"{cmd}今日运势",
        desc="查看今日运势",
        type="command",
    )
