from . import bot, entity, utils
from .bot import PLUGIN_DIR, PLUGIN_VERSION, db, util
from .bot.config import configs

__all__ = [
    "PLUGIN_DIR",
    "PLUGIN_VERSION",
    "bot",
    "configs",
    "db",
    "entity",
    "util",
    "utils",
]

API_VERSION = 1
