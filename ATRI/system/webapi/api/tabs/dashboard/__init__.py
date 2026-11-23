import nonebot
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from nonebot import require
from nonebot.config import Config

from ATRI.exceptions import str_traceback
from ATRI.log import log

from ....base_model import Result
from ....security import authentication
from .data_source import ApiDataSource
from .model import BotInfo

require("plugin_store")

router = APIRouter(prefix="/dashboard")

driver = nonebot.get_driver()


@router.get(
    "/get_bot_list",
    dependencies=[authentication()],
    response_model=Result[list[BotInfo]],
    response_class=JSONResponse,
    description="获取bot列表",  # type: ignore
)
async def _() -> Result[list[BotInfo]]:
    try:
        return Result.ok(await ApiDataSource.get_bot_list(), "拿到信息啦!")
    except Exception as e:
        log.error(f"{router.prefix}/get_bot_list 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.get(
    "/get_dashboard_stats",
    dependencies=[authentication()],
    response_class=JSONResponse,
    description="一次性获取仪表盘统计：今日/全部聊天调用计数 + 30 天趋势",
)
async def get_dashboard_stats(bot_id: str | None = None) -> Result[dict]:
    """合并原来的三个统计端点：仪表盘 30s 轮询从 3 次请求降为 1 次"""
    try:
        return Result.ok(
            {
                "count": await ApiDataSource.get_chat_and_call_count(bot_id),
                "all": await ApiDataSource.get_all_chat_and_call_count(bot_id),
                "month": await ApiDataSource.get_chat_and_call_month(bot_id),
            },
            "拿到信息啦!",
        )
    except Exception as e:
        log.error(f"{router.prefix}/get_dashboard_stats 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.get(
    "/get_nonebot_config",
    dependencies=[authentication()],
    response_model=Result[Config],
    response_class=JSONResponse,
    description="获取nb配置",  # type: ignore
)
async def _() -> Result[Config]:
    return Result.ok(driver.config)
