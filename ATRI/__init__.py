import nonebot
from nonebot.adapters.onebot.v11 import Adapter

from .configs import Config
from .dir import *
from .event.register import register_triggers

__version__ = "YHN-LK0-020"
"""版本号"""
__sub_version__ = "Release"
"""次版本号"""
__conf_path = Path(".") / "config.yml"
conf_m = Config(__conf_path)

conf = conf_m.config_model
"""机器人设置"""
service_list = {}
"""服务数据"""


def asgi():
    return nonebot.get_asgi()


def driver():
    return nonebot.get_driver()


def init():
    nonebot.init(**conf_m.get_runtime_conf())
    driver().register_adapter(Adapter)
    register_triggers()
    nonebot.load_plugins("ATRI/system")
    nonebot.load_plugins("plugins")
    nonebot.load_plugins("plugins/rss")
    from ATRI.service import driver_startup
    driver().on_startup(driver_startup)


def run():
    log_level = "debug" if conf.BotConfig.debug else "warning"
    nonebot.run(log_level=log_level)
