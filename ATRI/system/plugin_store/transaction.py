import os
import shutil
import tempfile
from pathlib import Path

from ATRI.dir import CONFIG_DIR, PLUGIN_CONFIG_DIR, PLUGIN_DATA_DIR
from ATRI.log import log

from . import paths


def backup_plugin_dir(path: Path) -> Path | None:
    """把插件目录整体复制到临时位置，供更新失败时恢复。

    更新走的是「下载覆盖」而不是原地拉取，下载失败时清理逻辑会把目标
    目录删掉——若事先不备份，旧版本就一起没了。
    """
    if not path.exists():
        return None
    backup = Path(tempfile.mkdtemp(prefix="atri-plugin-backup-")) / path.name
    try:
        if path.is_dir():
            shutil.copytree(path, backup)
        else:
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
    except Exception as e:
        log.warning(f"备份 {path} 失败，更新将无法回滚：{e}")
        shutil.rmtree(backup.parent, ignore_errors=True)
        return None
    return backup


def restore_plugin_dir(backup: Path | None, path: Path) -> bool:
    """用备份恢复插件目录；返回是否恢复成功。

    先拷到同级临时目录再换名：直接「删掉 → 拷贝」时，拷贝失败（Windows 文件
    占用、磁盘满）会把旧版本一起赔进去，而备份只在 ``%TEMP%`` 里。
    """
    if backup is None or not backup.exists():
        return False
    staging = path.with_name(f".{path.name}.restore")
    try:
        paths.safe_rmtree(staging)
        staging.parent.mkdir(parents=True, exist_ok=True)
        if backup.is_dir():
            shutil.copytree(backup, staging)
        else:
            shutil.copy2(backup, staging)
        if path.exists():
            if path.is_dir():
                if not paths.safe_rmtree(path):
                    raise OSError(f"删除 {path} 失败")
            else:
                path.unlink()
        os.replace(staging, path)
        log.warning(f"已从备份恢复插件目录 {path}")
        return True
    except Exception as e:
        log.error(f"从备份恢复 {path} 失败（备份仍在 {backup}）：{e}")
        paths.safe_rmtree(staging)
        return False


def drop_backup(backup: Path | None):
    if backup is None:
        return
    shutil.rmtree(backup.parent, ignore_errors=True)


def rollback_install(backup: Path | None, target: Path | None, existed: bool) -> bool:
    """安装失败时把落点恢复原状，返回是否恢复成功。

    ``existed`` 表示安装前目标是否已存在：已存在且有备份就还原，全新安装
    则清掉半成品。目标本来存在、备份却失败时不敢动它——此刻盘上的可能
    仍是旧版本，删掉就真丢了。
    """
    if target is None:
        return False
    if backup is not None and backup.exists():
        return restore_plugin_dir(backup, target)
    if existed:
        log.warning(f"{target} 无可用备份，保持原状")
        return False
    try:
        if target.is_dir():
            paths.safe_rmtree(target)
        elif target.exists():
            target.unlink()
    except OSError as e:
        log.error(f"清理安装残留 {target} 失败：{e}")
        return False
    return True


def remove_service_files(plugin_name: str):
    """删除插件残留的服务配置、插件设置与私有数据目录。

    这些文件都在 ``data/`` 下、不在插件目录里，只删插件目录会留下它们：
    重装同名插件会继承旧配置，卸载后也谈不上干净。
    """
    targets = [
        CONFIG_DIR / f"{plugin_name}.json",
        PLUGIN_CONFIG_DIR / f"{plugin_name}_config.json",
    ]
    for path in targets:
        try:
            if path.is_file():
                path.unlink()
                log.debug(f"已删除插件配置 {path}")
        except OSError as e:
            log.warning(f"删除插件配置 {path} 失败：{e}")
    data_dir = PLUGIN_DATA_DIR / plugin_name
    try:
        if data_dir.is_dir():
            paths.safe_rmtree(data_dir)
            log.debug(f"已删除插件数据目录 {data_dir}")
    except OSError as e:
        log.warning(f"删除插件数据目录 {data_dir} 失败：{e}")
