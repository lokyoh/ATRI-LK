import sys
from datetime import datetime
from pathlib import Path

from nonebot.log import logger as log

from ATRI import conf

LOGGER_DIR = Path(".") / "data" / "logs"
LOGGER_DIR.mkdir(exist_ok=True, parents=True)

LLM_LOG_EXTRA = "llm_io"
"""LLM 独立日志的标记，主日志会忽略带有该标记的记录"""

_NOW_TIME = datetime.now().strftime("%Y%m%d-%H")

_LOG_FORMAT = (
    "\033[36mATRI\033[0m "
    "| <g>{time:MM-DD HH:mm:ss}</g> "
    "| <lvl>{level}</lvl> "
    "<c><u>{name}</u></c> >> "
    "{message}"
)


class LoguruNameDealer:
    def __call__(self, record):
        if record["extra"].get(LLM_LOG_EXTRA):
            # LLM 输入输出使用 data/logs/llm 下的独立日志，不进入主日志
            return False
        log_handle = record["name"]
        if "nonebot.plugin.manager" in log_handle:
            plugin_name = log_handle.split(".")[-1]
            record["name"] = f"plugin.{plugin_name}"
        else:
            record["name"] = record["name"].split(".")[0]

        return record


log.remove()
log.add(
    sys.stdout,
    level="DEBUG" if conf.BotConfig.debug else "INFO",
    colorize=True,
    filter=LoguruNameDealer(),
    format=_LOG_FORMAT,
)

log.add(
    LOGGER_DIR / "info" / f"{_NOW_TIME}.log",
    rotation="10 MB",
    enqueue=True,
    level="INFO",
    encoding="utf-8",
    format=_LOG_FORMAT,
)

log.add(
    LOGGER_DIR / "warning" / f"{_NOW_TIME}.log",
    rotation="10 MB",
    enqueue=True,
    level="WARNING",
    encoding="utf-8",
    format=_LOG_FORMAT,
)

log.add(
    LOGGER_DIR / "error" / f"{_NOW_TIME}.log",
    rotation="10 MB",
    enqueue=True,
    level="ERROR",
    encoding="utf-8",
    format=_LOG_FORMAT,
)

log.add(
    LOGGER_DIR / "debug" / f"{_NOW_TIME}.log",
    rotation="10 MB",
    enqueue=True,
    level="DEBUG",
    encoding="utf-8",
    format=_LOG_FORMAT,
)
