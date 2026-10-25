from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ATRI.log import log

#: 单个数据文件的目标大小上限（字节）。超过就换新文件。
#: 1MB ≈ 数千条事件，够分析一周的量而不至于让磁盘无感膨胀。
MAX_FILE_BYTES = 1 * 1024 * 1024

#: 保留多少天的数据文件。超期的最早在日任务里清掉。
RETENTION_DAYS = 14

#: 正文预览的截断长度
PREVIEW_CHARS = 120


def _telemetry_dir() -> Path:
    from ..service import plugin

    path = plugin.get_path() / "telemetry"
    path.mkdir(parents=True, exist_ok=True)
    return path


class Telemetry:
    """采集器。

    **绝不抛异常**：埋点失败必须静默——它是旁路观测，
    不能因为磁盘满或权限问题把正常对话搞挂。
    """

    def __init__(self) -> None:
        self.enabled = True
        self.include_content = False
        self._lock = threading.Lock()

    def configure(self, enabled: bool, include_content: bool) -> None:
        self.enabled = bool(enabled)
        self.include_content = bool(include_content)

    def _current_file(self, root: Path) -> Path:
        day = datetime.now().strftime("%Y-%m-%d")
        candidate = root / f"{day}.jsonl"
        if candidate.exists() and candidate.stat().st_size >= MAX_FILE_BYTES:
            index = 1
            while True:
                rolled = root / f"{day}.{index}.jsonl"
                if not rolled.exists():
                    return rolled
                if rolled.stat().st_size < MAX_FILE_BYTES:
                    return rolled
                index += 1
        return candidate

    def record(self, event: str, **fields: Any) -> None:
        """写一条事件。任何异常都在这里吞掉。"""
        if not self.enabled:
            return
        try:
            payload: dict[str, Any] = {
                "ts": round(time.time(), 3),
                "event": str(event),
            }
            payload.update({k: v for k, v in fields.items() if v is not None})
            line = json.dumps(payload, ensure_ascii=False, default=str)
            root = _telemetry_dir()
            with self._lock:
                target = self._current_file(root)
                with open(target, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception as exc:
            log.debug(f"埋点写入失败（忽略）：{exc}")

    def preview(self, text: Any) -> str | None:
        """按配置返回正文预览；未开启时返回 None。"""
        if not self.include_content or not text:
            return None
        body = str(text)
        return body[:PREVIEW_CHARS] + ("…" if len(body) > PREVIEW_CHARS else "")

    def turn(
        self,
        *,
        chat_id: str,
        rounds: int,
        duration: float,
        hit_limit: bool,
        failed: bool = False,
        proactive: bool = False,
    ) -> None:
        self.record(
            "turn",
            chat_id=chat_id,
            rounds=rounds,
            duration=round(duration, 3),
            hit_limit=hit_limit,
            failed=failed,
            proactive=proactive,
        )

    def tool(
        self,
        *,
        name: str,
        ok: bool,
        duration: float,
        error: str | None = None,
    ) -> None:
        self.record(
            "tool",
            name=name,
            ok=ok,
            duration=round(duration, 3),
            error=(str(error)[:200] if error else None),
        )

    def judge(
        self,
        *,
        chat_id: str,
        replied: bool,
        duration: float,
        reason: str | None = None,
        text: Any = None,
    ) -> None:
        self.record(
            "judge",
            chat_id=chat_id,
            replied=replied,
            duration=round(duration, 3),
            reason=reason,
            content_preview=self.preview(text),
        )

    def initiative(self, *, chat_id: str, allowed: bool, reason: str = "") -> None:
        self.record(
            "initiative", chat_id=chat_id, allowed=allowed, reason=reason or None
        )

    def feel(self, *, mood: float, irritation: float, source: str) -> None:
        self.record(
            "feel",
            mood=round(float(mood), 3),
            irritation=round(float(irritation), 3),
            source=source,
        )

    def memory(
        self,
        *,
        queried: bool,
        hits: int,
        top_similarity: float | None = None,
        degraded: bool = False,
    ) -> None:
        self.record(
            "memory",
            queried=queried,
            hits=hits,
            top_similarity=(
                round(float(top_similarity), 3) if top_similarity is not None else None
            ),
            degraded=degraded,
        )


telemetry = Telemetry()


def prune_old_files(days: int = RETENTION_DAYS) -> int:
    """删除超期数据文件，返回删除数量。日任务调用。"""
    try:
        root = _telemetry_dir()
        cutoff = time.time() - days * 86400
        removed = 0
        for path in root.glob("*.jsonl"):
            try:
                if path.stat().st_mtime < cutoff:
                    os.remove(path)
                    removed += 1
            except OSError:
                continue
        if removed:
            log.info(f"清理了 {removed} 个过期的 Agent 行为数据文件")
        return removed
    except Exception as exc:
        log.debug(f"清理行为数据失败（忽略）：{exc}")
        return 0
