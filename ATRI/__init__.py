from pathlib import Path

import nonebot
from nonebot.adapters.onebot.v11 import Adapter

from .configs import Config

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
RES_DIR = Path(".") / "res"
"""资源路径"""
DATA_DIR = Path(".") / "data"
"""数据资源路径"""
FONT_DIR = RES_DIR / "font"
"""字体资源路径"""
IMG_DIR = RES_DIR / "img"
"""图片资源路径"""
RECORD_DIR = RES_DIR / "record"
"""声音资源路径"""
TEXT_DIR = RES_DIR / "text"
"""文本资源路径"""
HTML_DIR = RES_DIR / "html"
"""html资源路径"""
TEMP_DIR = Path(".") / "data" / "temp"
"""临时文件路径"""
TEMP_DIR.mkdir(parents=True, exist_ok=True)


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
