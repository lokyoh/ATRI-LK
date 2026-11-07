import os
import sys
from pathlib import Path

import nonebot
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ...base_model import Result
from ...utils import authentication

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
        # 执行重启
        os.execv(sys.executable, [sys.executable, *sys.argv])
    except Exception as e:
        # 释放重启锁
        restart_lock.release()
        return Result.fail(f"重启失败：{type(e).__name__}: {e}")


@router.post(
    "/update",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="更新到最新正式版",
)
async def _() -> Result:
    """触发系统自我更新。

    与 ``/更新`` 命令同一个实现，更新完成后需手动重启才会生效。
    """
    from ATRI.exceptions import str_traceback
    from ATRI.log import log
    from ATRI.system.update import Updater

    try:
        info = await Updater.update()
    except Exception as e:
        log.error(f"/configure/update 调用错误:{str_traceback(e)}")
        return Result.fail(f"更新失败：{type(e).__name__}: {e}")
    if "更新完成" in info:
        return Result.ok(info=info)
    return Result.fail(info=info)
