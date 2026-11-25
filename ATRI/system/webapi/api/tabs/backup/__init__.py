from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ATRI.exceptions import str_traceback
from ATRI.log import log

from ....base_model import Result
from ....security import authentication

router = APIRouter(prefix="/backup")

BACKUP_PREFIX = "atri-backup-"


class BackupItem(BaseModel):
    name: str
    """文件名（即备份时间）"""
    size: int
    """字节数"""
    mtime: float
    """最后修改时间戳"""


class BackupOverview(BaseModel):
    items: list[BackupItem]
    target_dir: str
    keep: int
    """保留份数；0 表示不轮转"""
    include_db: bool
    directories: list[str]
    auto_enabled: bool
    """自动备份开关"""
    cron: str
    """自动备份时间，形如 04:30"""


def _manager():
    from ATRI.system.backup import build_manager

    return build_manager()


@router.get(
    "/get_backups",
    dependencies=[authentication()],
    response_model=Result[BackupOverview],
    response_class=JSONResponse,
    description="列出已有备份与当前备份策略",
)
async def _() -> Result[BackupOverview]:
    try:
        from ATRI.system.backup import backup_config

        manager = _manager()
        items = [
            BackupItem(
                name=path.name,
                size=path.stat().st_size,
                mtime=path.stat().st_mtime,
            )
            for path in manager.list_backups()
        ]
        # 最新的排前面，前端不用再倒一次
        items.reverse()
        config = backup_config
        return Result.ok(
            BackupOverview(
                items=items,
                target_dir=str(config.get("target_dir") or ""),
                keep=int(config.get("keep") or 0),
                include_db=bool(config.get("include_db")),
                directories=list(config.get("directories") or []),
                auto_enabled=bool(config.get("enabled")),
                cron=f"{int(config.get('cron_hour') or 0):02d}:{int(config.get('cron_minute') or 0):02d}",
            ),
            "拿到信息啦!",
        )
    except Exception as e:
        log.error(f"{router.prefix}/get_backups 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/create_backup",
    dependencies=[authentication()],
    response_model=Result[BackupItem],
    response_class=JSONResponse,
    description="立即创建一份备份",
)
async def _() -> Result[BackupItem]:
    try:
        from pathlib import Path

        from ATRI.system.backup import create_backup

        file = Path(await create_backup())
        return Result.ok(
            BackupItem(
                name=file.name,
                size=file.stat().st_size,
                mtime=file.stat().st_mtime,
            ),
            "备份完成!",
        )
    except Exception as e:
        log.error(f"{router.prefix}/create_backup 调用错误:{str_traceback(e)}")
        return Result.fail(f"备份失败：{type(e).__name__}: {e}")


@router.post(
    "/delete_backup",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="删除某一份备份",
)
async def _(name: str) -> Result:
    try:
        # 只接受程序自己命名的文件，防止把任意路径传进来删掉别的东西。
        if not name.startswith(BACKUP_PREFIX) or "/" in name or "\\" in name:
            return Result.fail("备份名不合法", 400)
        if not name.endswith(".zip"):
            return Result.fail("备份名不合法", 400)
        manager = _manager()
        target = next(
            (path for path in manager.list_backups() if path.name == name), None
        )
        if target is None:
            return Result.fail(f"备份 {name} 不存在", 404)
        target.unlink()
        return Result.ok(info="删除成功!")
    except Exception as e:
        log.error(f"{router.prefix}/delete_backup 调用错误:{str_traceback(e)}")
        return Result.fail(f"删除失败：{type(e).__name__}: {e}")


__all__ = ["BackupItem", "BackupOverview", "router"]
