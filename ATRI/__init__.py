import nonebot

from .configs import Config
from .dir import * # noqa: F403

__version__ = "YHN-LK0-020"
"""版本号"""
__sub_version__ = "Patch1"
"""次版本号"""
__conf_path = Path(".") / "config.yml"
conf_m = Config(__conf_path)

conf = conf_m.config_model
"""机器人设置"""


def asgi():
    return nonebot.get_asgi()


def driver():
    return nonebot.get_driver()


def init():
    nonebot.init(**conf_m.get_runtime_conf())
    from nonebot.adapters.onebot.v11 import Adapter
    driver().register_adapter(Adapter)
    from ATRI.event.register import register_triggers
    register_triggers()
    nonebot.load_plugins("ATRI/system")
    nonebot.load_plugins("plugins")
    nonebot.load_plugins("plugins/rss")
    from ATRI.service import driver_startup
    driver().on_startup(driver_startup)


def run():
    log_level = "debug" if conf.BotConfig.debug else "warning"
    nonebot.run(log_level=log_level)
