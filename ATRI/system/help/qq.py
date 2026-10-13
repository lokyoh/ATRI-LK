from nonebot.adapters.qq.models import Action, Button, Permission, RenderData

from ATRI.bot import BotUtils
from ATRI.bot.model import PanelItem
from ATRI.event import ATRIEventBus, Priority

cmd = BotUtils.get_command_start()
BTN_MENU = Button(
    id="btn_menu",
    render_data=RenderData(label="菜单", style=1),
    action=Action(
        type=2,
        permission=Permission(type=2),
        data=f"{cmd}菜单",
        enter=True,
    ),
)


@ATRIEventBus.subscribe("set_panel", priority=Priority.HIGH)
def get_help_menu_item(event):
    return PanelItem(
        name=f"{cmd}服务列表",
        desc="获取服务列表",
        type="command",
    )
