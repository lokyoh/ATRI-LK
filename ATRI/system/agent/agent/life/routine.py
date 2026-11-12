"""固定作息层。

`routine.md` 是「每天都必须发生」的骨架：睡觉、起床、三餐、睡前准备。
它不由模型生成，也不写进每日 JSON；读取时实时合并，管理员改文件即生效。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ATRI.dir import RES_DATA_DIR
from ATRI.log import log

from .model import (
    DAY_END_MIN,
    DAY_START_MIN,
    MAX_ROUTINE_SLOTS,
    EntryKind,
    ScheduleEntry,
    format_time,
    parse_time,
)

BUILTIN_ROUTINE_FILE = RES_DATA_DIR / "agent" / "life" / "routine.md"
"""随仓库发布的固定作息"""

DEFAULT_ROUTINE = """# 固定作息

| 开始 | 结束 | 地点 | 活动 | 保护 |
|---|---|---|---|---|
| 02:00 | 06:00 | 家 | 睡觉 | 是 |
| 06:00 | 06:40 | 家 | 起床、洗漱 | 是 |
| 07:20 | 08:00 | 家 | 早饭 | 是 |
| 12:00 | 12:40 | 家 | 午饭 | 是 |
| 18:30 | 19:10 | 家 | 晚饭 | 是 |
| 23:00 | 02:00 | 家 | 洗漱、准备睡觉 | 是 |
"""

_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
_SEPARATOR_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
_TRUE = {"是", "true", "1", "yes", "y", "on"}
_FALSE = {"否", "false", "0", "no", "n", "off"}


@dataclass
class Routine:
    slots: list[ScheduleEntry]
    source: str = "builtin"
    """builtin / user / default"""

    def protected_slots(self) -> list[ScheduleEntry]:
        return [slot for slot in self.slots if slot.note == "protected"]

    def gaps(self) -> list[tuple[int, int]]:
        """当天窗口内没有被固定作息覆盖的空档（分钟）。"""
        covered = sorted((slot.start_min, slot.end_min) for slot in self.slots)
        gaps: list[tuple[int, int]] = []
        cursor = DAY_START_MIN
        for start, end in covered:
            if start > cursor:
                gaps.append((cursor, start))
            cursor = max(cursor, end)
        if cursor < DAY_END_MIN:
            gaps.append((cursor, DAY_END_MIN))
        return gaps

    def slot_at(self, minute: int) -> ScheduleEntry | None:
        for slot in self.slots:
            if slot.contains(minute):
                return slot
        return None


def _split_row(line: str) -> list[str]:
    match = _ROW_RE.match(line)
    if not match:
        return []
    return [cell.strip() for cell in match.group(1).split("|")]


def parse_routine(text: str) -> list[ScheduleEntry]:
    """解析 Markdown 表格；无表头/无有效行时返回空列表。"""
    slots: list[ScheduleEntry] = []
    columns: list[str] | None = None
    for line in (text or "").splitlines():
        if _SEPARATOR_RE.match(line):
            continue
        cells = _split_row(line)
        if not cells:
            continue
        if columns is None:
            if "开始" in cells and "结束" in cells:
                columns = cells
            continue
        row = dict(zip(columns, cells, strict=False))
        raw_start = row.get("开始") or row.get("start") or ""
        raw_end = row.get("结束") or row.get("end") or ""
        if not raw_start or not raw_end:
            continue
        try:
            start = parse_time(raw_start)
            end = parse_time(raw_end)
        except ValueError as exc:
            log.warning(f"固定作息行格式错误，已跳过：{line.strip()}（{exc}）")
            continue
        place = row.get("地点") or row.get("place") or ""
        activity = row.get("活动") or row.get("activity") or ""
        protected_raw = str(row.get("保护") or row.get("protected") or "是").lower()
        protected = protected_raw in _TRUE or protected_raw not in _FALSE
        slots.append(
            ScheduleEntry(
                start=start,
                end=end,
                kind=EntryKind.STAY,
                place=place,
                activity=activity,
                source="routine",
                note="protected" if protected else "",
            )
        )
        if len(slots) >= MAX_ROUTINE_SLOTS:
            log.warning(f"固定作息条目超过上限 {MAX_ROUTINE_SLOTS}，其余已忽略")
            break
    return slots


def load_routine(user_dir: Path | None = None) -> Routine:
    """用户目录存在 `routine.md` 时优先使用；否则读内置，最后回退代码默认值。"""
    candidates: list[tuple[Path, str]] = []
    if user_dir is not None:
        candidates.append((user_dir / "routine.md", "user"))
    candidates.append((BUILTIN_ROUTINE_FILE, "builtin"))

    for path, source in candidates:
        if not path.is_file():
            continue
        try:
            slots = parse_routine(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            log.warning(f"固定作息文件 {path} 读取失败，跳过：{exc}")
            continue
        if slots:
            return Routine(slots=_normalize(slots), source=source)
        log.warning(f"固定作息文件 {path} 没有有效条目，跳过")

    slots = parse_routine(DEFAULT_ROUTINE)
    log.warning("未找到可用的固定作息，使用代码内置默认作息")
    return Routine(slots=_normalize(slots), source="default")


def _normalize(slots: list[ScheduleEntry]) -> list[ScheduleEntry]:
    return sorted(slots, key=lambda slot: slot.start_min)


def describe_gaps(routine: Routine) -> str:
    """给模型看的空档清单，例如 `08:00-12:00、12:40-18:30`。"""
    return "、".join(
        f"{format_time(start)}-{format_time(end)}" for start, end in routine.gaps()
    )
