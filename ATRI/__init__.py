from time import sleep
from pathlib import Path

import nonebot
from nonebot.adapters.onebot.v11 import Adapter

from .configs import Config

__version__ = "YHN-LK0-005-PRE3"
__conf_path = Path(".") / "config.yml"
__conf = Config(__conf_path)

conf = __conf.parse()
RES_DIR = Path(".") / "res"
FONT_DIR = RES_DIR / "font"
IMG_DIR = RES_DIR / "img"
RECORD_DIR = RES_DIR / "record"
TEXT_DIR = RES_DIR / "text"
TEMP_DIR = Path(".") / "data" / "temp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)


def asgi():
    return nonebot.get_asgi()


def driver():
    return nonebot.get_driver()


def init():
    nonebot.init(**__conf.get_runtime_conf())
    driver().register_adapter(Adapter)
    nonebot.load_plugins("ATRI/plugins")
    nonebot.load_plugins("ATRI/plugins/rss")
    sleep(3)


def run():
    log_level = "debug" if conf.BotConfig.debug else "warning"
    nonebot.run(log_level=log_level)
