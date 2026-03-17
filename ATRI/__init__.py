import nonebot

from .configs import Config
from .dir import *  # noqa: F403

__version__ = "YHN-LK0-020"
"""版本号"""
__sub_version__ = "Patch6"
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
    from ATRI.load import load_atri
    load_atri()


def run():
    log_level = "debug" if conf.BotConfig.debug else "warning"
    nonebot.run(log_level=log_level)
