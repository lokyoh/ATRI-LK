import os
import sys
from pathlib import Path

import nonebot
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ...base_model import Result

router = APIRouter(prefix="/configure")

driver = nonebot.get_driver()

port = driver.config.port

# 重启临时文件路径
PLUGIN_DIR = Path(".") / "data" / "plugins" / "restart"
RESTART_TEMP = PLUGIN_DIR / "restart_temp"
PLUGIN_DIR.mkdir(parents=True, exist_ok=True)


@router.post(
    "/restart",
    response_model=Result,
    response_class=JSONResponse,
    description="重启",
)
async def _() -> Result:
    from ATRI.system.restart import restart_lock

    # 检查锁是否可用 (非阻塞尝试获取锁)
    if not restart_lock.acquire(blocking=False):
        return Result.fail("系统正在重启中，请勿重复操作")

    try:
        # 保存重启标记，用于重启后通知用户
        with open(RESTART_TEMP, "w", encoding="utf8") as f:
            f.write("webapi_restart")

        # 执行重启
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        # 释放重启锁
        restart_lock.release()
        return Result.fail(f"重启失败：{type(e).__name__}: {e}")
