from ATRI.permission import ADMIN, MASTER
from ATRI.service import Service

from .util import PLUGIN_VERSION, on_startup

plugin = Service(
    "用户", "ATRI的综合性用户系统插件", PLUGIN_VERSION, Service.ServiceType.LKPLUGIN
)
plugin_admin = Service(
    "群管",
    "ATRI的综合性插件的群聊管理员指令部分",
    PLUGIN_VERSION,
    Service.ServiceType.LKPLUGIN,
).permission(ADMIN)
plugin_master = Service(
    "主人",
    "ATRI的综合性插件的主人专用指令部分",
    PLUGIN_VERSION,
    Service.ServiceType.LKPLUGIN,
).permission(MASTER)

from . import handlers as handlers  # noqa: E402

plugin.on_startup(on_startup)
