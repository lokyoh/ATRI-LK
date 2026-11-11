from nonebot.adapters.qq.models import Action, Button, Permission, RenderData

from ATRI.bot.model import PanelItem
from ATRI.bot.utils import BotUtils
from ATRI.event import ATRIEventBus

cmd = BotUtils.get_command_start()
BTN_SIGNIN = Button(
    id="btn_signin",
    render_data=RenderData(label="签到", style=1),
    action=Action(
        type=2,
        permission=Permission(type=2),
        data=f"{cmd}签到",
        enter=True,
    ),
)


@ATRIEventBus.subscribe("set_panel")
def get_signin_menu_item(event):
    return PanelItem(
        name=f"{cmd}签到",
        desc="亚托莉的签到系统",
        type="command",
    )
