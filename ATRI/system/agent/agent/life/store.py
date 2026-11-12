"""按日 JSON 的读写：日程、穿搭、位置快照。

所有写入先写临时文件再 `os.replace()`；旧文件只保留最近 N 天。
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path

from ATRI.log import log

RETENTION_DAYS = 7
"""日程 / 穿搭保留天数"""


def read_json(path: Path) -> dict:
    """读 JSON；缺失或损坏返回空字典，不阻塞聊天。"""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        log.warning(f"读取 {path} 失败，按空数据处理：{exc}")
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: Path, payload: dict) -> None:
    """原子写 JSON；目录不存在时自动创建。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        with suppress(OSError):
            os.unlink(tmp_path)
        raise


def prune_dated_files(directory: Path, keep: int = RETENTION_DAYS) -> int:
    """删除目录下过期的 `YYYY-MM-DD.json`，返回删除数量。"""
    if not directory.is_dir():
        return 0
    cutoff = datetime.now().date() - timedelta(days=max(1, keep))
    removed = 0
    for path in directory.glob("*.json"):
        try:
            file_date = datetime.strptime(path.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date < cutoff:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed
