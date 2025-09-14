import nonebot
from nonebot.adapters.onebot.v11 import Adapter

from .configs import Config
from .dir import *

__version__ = "YHN-LK0-016"
"""版本号"""
__sub_version__ = "Patch5"
"""次版本号"""
__conf_path = Path(".") / "config.yml"
__conf = Config(__conf_path)

conf = __conf.parse()
"""机器人设置"""
service_list = {}
"""服务数据"""


def asgi():
    return nonebot.get_asgi()


def driver():
    return nonebot.get_driver()


def init():
    nonebot.init(**__conf.get_runtime_conf())
    driver().register_adapter(Adapter)
    nonebot.load_plugins("ATRI/system")
    nonebot.load_plugins("plugins")
    nonebot.load_plugins("plugins/rss")
    from ATRI.service import driver_startup
    driver().on_startup(driver_startup)


def run():
    log_level = "debug" if conf.BotConfig.debug else "warning"
    nonebot.run(log_level=log_level)
