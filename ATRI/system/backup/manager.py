import asyncio
import os
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from ATRI.log import log

BACKUP_PREFIX = "atri-backup-"

DEFAULT_DIRECTORIES = ["data"]

DEFAULT_TARGET_DIR = "data/backups"

DEFAULT_DB_DIR = "data/sql"

DEFAULT_TEMP_DIR = "data/temp"


class BackupManager:
    """按配置把目录与数据库打包成 zip。"""

    def __init__(
        self,
        base_dir: Path | str | None = None,
        *,
        directories: list[str] | None = None,
        target_dir: str = DEFAULT_TARGET_DIR,
        keep: int = 7,
        include_db: bool = True,
        db_dir: str = DEFAULT_DB_DIR,
        temp_dir: str = DEFAULT_TEMP_DIR,
    ) -> None:
        self.base_dir = Path(base_dir or ".").resolve()
        self.directories = list(directories or DEFAULT_DIRECTORIES)
        self.target_dir = self._absolute(target_dir)
        self.keep = int(keep)
        self.include_db = bool(include_db)
        self.db_dir = self._absolute(db_dir)
        self.temp_dir = self._absolute(temp_dir)

    def _absolute(self, path: str | Path) -> Path:
        candidate = Path(path)
        if candidate.is_absolute():
            return candidate.resolve()
        return (self.base_dir / candidate).resolve()

    def list_backups(self) -> list[Path]:
        """已有的备份，按文件名（即时间）从旧到新。"""
        if not self.target_dir.is_dir():
            return []
        return sorted(
            (
                path
                for path in self.target_dir.glob(f"{BACKUP_PREFIX}*.zip")
                if path.is_file()
            ),
            key=lambda path: path.name,
        )

    async def create_backup(self) -> Path:
        """创建一份备份，返回 zip 路径。"""
        return await asyncio.to_thread(self._create_backup)

    def _create_backup(self) -> Path:
        from ATRI.utils.datetime import now

        self.target_dir.mkdir(parents=True, exist_ok=True)
        stamp = now().strftime("%Y%m%d-%H%M%S")
        final = self.target_dir / f"{BACKUP_PREFIX}{stamp}.zip"
        suffix = 1
        while final.exists():
            final = self.target_dir / f"{BACKUP_PREFIX}{stamp}-{suffix}.zip"
            suffix += 1

        staging = Path(tempfile.mkdtemp(prefix="atri-backup-"))
        try:
            if self.include_db:
                self._snapshot_databases(staging)
            self._copy_directories(staging)
            self._zip(staging, final)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        self._rotate()
        return final

    def _snapshot_databases(self, staging: Path) -> None:
        if not self.db_dir.is_dir():
            return
        for source in sorted(self.db_dir.glob("*.sqlite3")):
            if not source.is_relative_to(self.base_dir):
                log.warning(f"数据库不在项目目录内，跳过：{source}")
                continue
            target = staging / source.relative_to(self.base_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with (
                    sqlite3.connect(
                        f"file:{source.as_posix()}?mode=ro", uri=True
                    ) as src,
                    sqlite3.connect(target) as dst,
                ):
                    src.backup(dst)
            except sqlite3.Error as e:
                log.warning(f"备份数据库 {source.name} 失败：{e}")

    def _excluded(self, path: Path) -> bool:
        resolved = path.resolve()
        for blocked in (self.target_dir, self.temp_dir, self.db_dir):
            if resolved == blocked or resolved.is_relative_to(blocked):
                return True
        return False

    def _copy_directories(self, staging: Path) -> None:
        for raw in self.directories:
            root = self._absolute(raw)
            if not root.is_dir():
                log.warning(f"备份目录不存在，跳过：{root}")
                continue
            if self._excluded(root):
                continue
            for item in root.rglob("*"):
                if not item.is_file() or self._excluded(item):
                    continue
                if not item.is_relative_to(self.base_dir):
                    log.warning(f"文件不在项目目录内，跳过：{item}")
                    continue
                target = staging / item.relative_to(self.base_dir)
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(item, target)
                except OSError as e:
                    log.warning(f"备份文件 {item} 失败：{e}")

    def _zip(self, staging: Path, final: Path) -> None:
        tmp = final.with_suffix(final.suffix + ".tmp")
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
                for item in sorted(staging.rglob("*")):
                    if item.is_file():
                        archive.write(item, item.relative_to(staging).as_posix())
            os.replace(tmp, final)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def _rotate(self) -> None:
        if self.keep <= 0:
            return
        backups = self.list_backups()
        for old in backups[: max(0, len(backups) - self.keep)]:
            try:
                old.unlink()
                log.info(f"已删除旧备份：{old.name}")
            except OSError as e:
                log.warning(f"删除旧备份 {old.name} 失败：{e}")
