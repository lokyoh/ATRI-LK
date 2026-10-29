"""lkpet · 赛博宠物养成插件。

本文件只负责服务注册与插件设置。**全部指令与事件处理器都在 ``handlers/`` 里**，
指令 matcher 在 ``matchers.py`` 注册。
"""

from ATRI.service import Service

from .config import LKPetConfig

plugin = Service(
    "宠物",
    "赛博宠物插件demo",
    "0.4.0",
    Service.ServiceType.GAME,
).main_cmd("宠物")
config: LKPetConfig = plugin.add_plugin_config(LKPetConfig)

from . import handlers as handlers  # noqa: E402
