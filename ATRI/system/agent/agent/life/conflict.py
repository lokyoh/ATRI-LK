"""填入与固定作息的冲突处理。

规则（管理员确认）：

- 填入的开始时间不能早于现在。
- 正在进行的骨架不挪动，只把填入往后推。
- 还没到的骨架：挪动不超过 SHIFT_LIMIT_MIN 就整体挪（可连锁推挤其他骨架），
  挪不动就把填入拆成两段绕开（要求每段至少 MIN_SEGMENT_MIN 分钟）。

调整只影响当天，routine.md 不会被改写。
"""

from __future__ import annotations

from .model import (
    DAY_END_MIN,
    DAY_START_MIN,
    EntryKind,
    ScheduleEntry,
    format_time,
    parse_time,
)

#: 单条骨架最多挪动的分钟数；超过就改用拆段
SHIFT_LIMIT_MIN = 60

#: 拆段后每一段的最小长度，太短的碎片直接丢弃
MIN_SEGMENT_MIN = 5


#: 一次处理最多走多少步，防止病态输入死循环
MAX_STEPS = 200


def _clone(entry: ScheduleEntry, *, start: int, end: int) -> ScheduleEntry:
    return ScheduleEntry(
        start=parse_time(format_time(start)),
        end=parse_time(format_time(end)),
        kind=entry.kind,
        activity=entry.activity,
        place=entry.place,
        from_place=entry.from_place,
        to_place=entry.to_place,
        source=entry.source,
        note=entry.note,
        origin_start=entry.origin_start,
        cross_from=entry.cross_from,
    )


def _hit(slots: list[ScheduleEntry], start: int, end: int, skip: int = -1) -> int:
    for index, slot in enumerate(slots):
        if index != skip and slot.overlaps(start, end):
            return index
    return -1


def _fits(slots: list[ScheduleEntry], start: int, duration: int, skip: int) -> bool:
    if start < DAY_START_MIN or start + duration > DAY_END_MIN:
        return False
    return _hit(slots, start, start + duration, skip) == -1


def _shift(
    slots: list[ScheduleEntry],
    index: int,
    blocker: ScheduleEntry,
    frozen: set[int],
) -> list[ScheduleEntry] | None:
    """把 slots[index] 挪开以避开 blocker；挪不动或会撞上别人就返回 None。

    只挪这一条，不连锁推挤别的骨架——否则一次推迟能把整张作息表拖偏。
    """
    if index in frozen:
        return None
    slot = slots[index]
    duration = slot.duration
    if duration <= 0:
        return None
    for start in (blocker.start_min - duration, blocker.end_min):
        if start == slot.start_min or abs(start - slot.start_min) > SHIFT_LIMIT_MIN:
            continue
        if not _fits(slots, start, duration, index):
            continue
        moved = list(slots)
        moved[index] = _clone(slot, start=start, end=start + duration)
        return moved
    return None


def _split(entry: ScheduleEntry, slot: ScheduleEntry) -> list[ScheduleEntry]:
    """绕开骨架拆成两段；每段只保留自己那段真正开始的时间作为起点。"""
    parts: list[ScheduleEntry] = []
    origin = entry.origin_start or format_time(entry.start_min)
    if slot.start_min - entry.start_min >= MIN_SEGMENT_MIN:
        head = _clone(entry, start=entry.start_min, end=slot.start_min)
        head.origin_start = origin
        parts.append(head)
    if entry.end_min - slot.end_min >= MIN_SEGMENT_MIN:
        tail = _clone(entry, start=slot.end_min, end=entry.end_min)
        tail.origin_start = ""
        parts.append(tail)
    return parts


def _verify(slots: list[ScheduleEntry], placed: list[ScheduleEntry]) -> list[str]:
    problems: list[str] = []
    for index, slot in enumerate(slots):
        for other in placed:
            if slot.overlaps(other.start_min, other.end_min):
                problems.append(
                    f"{format_time(slot.start)}-{format_time(slot.end)}「{slot.activity}」"
                    f"与 {format_time(other.start)}-{format_time(other.end)} 重叠"
                )
    return problems


def resolve(
    entries: list[ScheduleEntry],
    slots: list[ScheduleEntry],
    now_min: int | None,
    frozen_extra: set[int] | None = None,
) -> tuple[list[ScheduleEntry], list[ScheduleEntry], list[str], list[str]]:
    """返回 (骨架, 填入, 提示, 失败原因)。失败原因非空时前两项都为空。

    ``frozen_extra`` 是额外不许挪动的骨架下标（例如昨天跨零点的尾巴）。
    """
    skeleton = [_clone(slot, start=slot.start_min, end=slot.end_min) for slot in slots]
    placed: list[ScheduleEntry] = []
    warnings: list[str] = []
    # 正在进行的骨架不许挪，只能让填入绕开它。
    frozen = {
        index
        for index, slot in enumerate(skeleton)
        if now_min is not None and slot.start_min <= now_min < slot.end_min
    } | set(frozen_extra or ())

    for entry in sorted(entries, key=lambda item: item.start_min):
        if now_min is not None and entry.start_min < now_min:
            return [], [], [], [
                f"{format_time(entry.start)} 已经过去，填入不能早于现在"
            ]
        pending = [entry]
        steps = 0
        while pending:
            steps += 1
            if steps > MAX_STEPS:
                return [], [], [], ["日程冲突太多，无法安排"]
            part = pending.pop(0)
            hit = _hit(skeleton, part.start_min, part.end_min)
            if hit == -1:
                placed.append(part)
                continue
            slot = skeleton[hit]
            running = hit in frozen
            if not running:
                shifted = _shift(skeleton, hit, part, frozen)
                if shifted is not None:
                    skeleton = shifted
                    pending.insert(0, part)
                    continue
                if part.kind is EntryKind.STAY:
                    parts = _split(part, slot)
                    if parts:
                        warnings.append(
                            f"{format_time(part.start)}-{format_time(part.end)} 被"
                            f"「{slot.activity}」拆成 {len(parts)} 段"
                        )
                        pending = parts + pending
                        continue
            # 挪不开或正在进行：把填入推到骨架之后
            delayed = _clone(part, start=slot.end_min, end=slot.end_min + part.duration)
            if delayed.end_min > DAY_END_MIN:
                return [], [], [], [
                    f"{format_time(part.start)}-{format_time(part.end)} 撞上"
                    f"「{slot.activity}」，往后挪会超出当天"
                ]
            warnings.append(
                f"{format_time(part.start)}-{format_time(part.end)} 撞上"
                f"「{slot.activity}」，已推迟到 {format_time(delayed.start)}"
            )
            pending.insert(0, delayed)

    problems = _verify(skeleton, placed)
    if problems:
        return [], [], [], problems
    placed.sort(key=lambda item: item.start_min)
    skeleton.sort(key=lambda item: item.start_min)
    return skeleton, placed, warnings, []
