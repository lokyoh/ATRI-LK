from ATRI.log import log
from ATRI.permission import MASTER
from ATRI.service import Service

from .config import BackupConfig
from .manager import BackupManager

plugin = (
    Service("备份", "备份 data 目录与数据库", "0.1.0", Service.ServiceType.SYSTEM)
    .allow_switch(False)
    .permission(MASTER)
)

backup_config: BackupConfig = plugin.add_plugin_config(BackupConfig)


def build_manager() -> BackupManager:
    """按当前配置创建管理器。配置就是事实来源，改完下一次调用即生效。"""
    return BackupManager(
        directories=list(backup_config.get("directories") or ["data"]),
        target_dir=str(backup_config.get("target_dir") or "data/backups"),
        keep=int(backup_config.get("keep") or 0),
        include_db=bool(backup_config.get("include_db")),
    )


async def create_backup() -> str:
    path = await build_manager().create_backup()
    log.info(f"备份完成：{path}")
    return str(path)


async def scheduled_backup() -> None:
    """定时任务入口：失败只记日志，不打断调度器。"""
    try:
        await create_backup()
    except Exception as e:
        log.error(f"自动备份失败：{e}")


backup = plugin.on_command("备份", "立即备份 data 目录与数据库")


@backup.handle()
async def _():
    await backup.finish(f"备份完成：{await create_backup()}")


backup_list = plugin.on_command("备份列表", "列出已有的备份")


@backup_list.handle()
async def _():
    backups = build_manager().list_backups()
    if not backups:
        await backup_list.finish("还没有备份。")
    lines = [
        f"{path.name}（{path.stat().st_size / 1024 / 1024:.1f} MB）" for path in backups
    ]
    await backup_list.finish("已有备份：\n" + "\n".join(lines))


if backup_config.get("enabled"):
    plugin.scheduler_jobs().add_job(
        scheduled_backup,
        "自动备份",
        "cron",
        hour=int(backup_config.get("cron_hour") or 0),
        minute=int(backup_config.get("cron_minute") or 0),
        persist=False,
        misfire_grace_time=3600,
    )
