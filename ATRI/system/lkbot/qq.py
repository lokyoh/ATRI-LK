from ATRI.bot.model import PanelItem
from ATRI.bot.utils import BotUtils
from ATRI.event import ATRIEventBus, Priority
from ATRI.message import QQMessage

cmd = BotUtils.get_command_start()
CMD_BIND = QQMessage.cmd(f"{cmd}绑定")
BIND_TIP = f"还未绑定名称哟，使用指令 {CMD_BIND} 进行绑定"


@ATRIEventBus.subscribe("set_panel", priority=Priority.HIGH)
def get_info_menu_item(event):
    return PanelItem(
        name=f"{cmd}我的信息",
        desc="查询自己的信息",
        type="command",
    )


@ATRIEventBus.subscribe("set_panel", priority=Priority.HIGH)
def get_backpack_menu_item(event):
    return PanelItem(
        name=f"{cmd}我的背包",
        desc="查看背包中的内容",
        type="command",
    )
