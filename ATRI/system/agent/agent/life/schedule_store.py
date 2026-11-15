"""填入式日程：校验、合并、覆盖/截断、位置派生。"""

from __future__ import annotations

import datetime
import itertools
from dataclasses import dataclass, field
from pathlib import Path

from ATRI.log import log
from ATRI.utils.datetime import now

from .conflict import resolve as resolve_conflicts
from .model import (
    DAY_END_MIN,
    DAY_LAST_END_MIN,
    DAY_START_MIN,
    HOME_PLACE,
    MAX_ACTIVITY_CHARS,
    MAX_FILL_ENTRIES,
    DayPlan,
    EntryKind,
    LocationState,
    ScheduleEntry,
    day_minutes,
    format_time,
    get_now_date,
    now_minutes,
    parse_time,
)
from .routine import Routine, load_routine
from .store import read_json, write_json

TRAVEL_MIN_MINUTES = 5
TRAVEL_DEFAULT_MINUTES = 15
TRAVEL_MAX_MINUTES = 45

#: 跨零点条目平移到当天后的标记
CARRIED_NOTE = "carried"


def _shift_date(date: str, days: int) -> str:
    try:
        cur = datetime.datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return ""
    return (cur + datetime.timedelta(days=days)).strftime("%Y-%m-%d")


@dataclass
class FillResult:
    ok: bool
    plan: DayPlan | None = None
    message: str = ""
    warnings: list[str] = field(default_factory=list)
    entries: list[ScheduleEntry] = field(default_factory=list)


class ScheduleStore:
    def __init__(self, root: Path, user_dir: Path | None = None):
        self.root = root
        self.schedule_dir = root / "schedule"
        self.user_dir = user_dir

    # ---- 读写 ----

    def path_for(self, date: str) -> Path:
        return self.schedule_dir / f"{date}.json"

    def load(self, date: str) -> DayPlan:
        data = read_json(self.path_for(date))
        if not data or data.get("date") != date:
            return DayPlan(date=date)
        try:
            return DayPlan.from_dict(data)
        except (ValueError, TypeError) as exc:
            log.warning(f"日程文件 {date} 解析失败，按空计划处理：{exc}")
            return DayPlan(date=date)

    def save(self, plan: DayPlan) -> None:
        plan.updated_at = now().timestamp()
        plan.version += 1
        write_json(self.path_for(plan.date), plan.to_dict())

    def clear(self, date: str) -> bool:
        """清掉填入层与当天挪动过的骨架，并收回去同步到次日的副本。"""
        plan = self.load(date)
        changed = bool(plan.entries or plan.routine)
        plan.entries = []
        plan.routine = []
        if changed:
            self.save(plan)
        # 跨天条目在次日留了副本：源头没了，副本也要撤掉。
        next_date = _shift_date(date, 1)
        if next_date and self._drop_synced(self.load(next_date), date):
            pass
        return changed

    def remove_entry(
        self, date: str, start: str, end: str, activity: str = ""
    ) -> bool:
        """删掉匹配的一条填入条目；骨架不受影响。"""
        plan = self.load(date)
        low = day_minutes(parse_time(start))
        high = day_minutes(parse_time(end))
        if high <= low:
            high += 24 * 60
        target = [
            entry
            for entry in plan.entries
            if entry.start_min == low
            and entry.end_min == high
            and (not activity or entry.activity == activity)
        ]
        if not target:
            return False
        plan.entries = [entry for entry in plan.entries if entry not in target]
        self.save(plan)
        next_date = _shift_date(date, 1)
        if next_date:
            next_plan = self.load(next_date)
            for entry in target:
                if entry.end_min <= 24 * 60:
                    continue
                piece_low = max(0, entry.start_min - 24 * 60)
                piece_high = entry.end_min - 24 * 60
                next_plan.entries = [
                    item
                    for item in next_plan.entries
                    if not (
                        item.start_min == piece_low
                        and item.end_min == piece_high
                        and item.activity == entry.activity
                    )
                ]
            self.save(next_plan)
        return True

    def _drop_synced(self, plan: DayPlan, source_date: str) -> bool:
        """删掉「同步自 source_date」的副本条目；有改动返回 True。"""
        kept = [entry for entry in plan.entries if entry.cross_from != source_date]
        if len(kept) == len(plan.entries):
            return False
        plan.entries = kept
        self.save(plan)
        return True

    # ---- 合并视图 ----

    def routine(self) -> Routine:
        return load_routine(self.user_dir)

    def routine_slots(self, date: str | None = None) -> list[ScheduleEntry]:
        """这一天的骨架：当天被挪动过就用存档里的，否则读 routine.md。"""
        plan = self.load(date or get_now_date())
        return list(plan.routine or self.routine().slots)

    def carried_entries(self, date: str | None = None) -> list[ScheduleEntry]:
        """前一天跨过零点的尾巴，平移到当天时间轴（可能为空）。"""
        today = date or get_now_date()
        prev = _shift_date(today, -1)
        if not prev:
            return []
        prev_plan = self.load(prev)
        own = self.load(today).entries
        carried: list[ScheduleEntry] = []
        for entry in list(prev_plan.entries) + list(
            prev_plan.routine or self.routine().slots
        ):
            if entry.end_min <= 24 * 60:
                continue
            start = max(0, entry.start_min - 24 * 60)
            end = entry.end_min - 24 * 60
            if end <= start:
                continue
            origin = entry.origin_start or format_time(entry.start_min)
            item = self._clone(
                entry, start=format_time(start), end=format_time(end)
            )
            item.note = CARRIED_NOTE
            item.origin_start = origin
            # 写入时已经同步到次日的，不再重复补一条。
            same = any(
                x.start_min == item.start_min
                and x.end_min == item.end_min
                and x.activity == item.activity
                for x in own
            )
            if same:
                continue
            carried.append(item)
        return carried

    def merged(self, date: str | None = None) -> list[ScheduleEntry]:
        """固定作息 + 填入层 + 前一天的遗留尾巴，按开始时间排序。"""
        date = date or get_now_date()
        plan = self.load(date)
        entries = list(plan.entries) + self.routine_slots(date) + self.carried_entries(date)
        return sorted(entries, key=lambda entry: entry.start_min)

    def display_entries(self, date: str | None = None) -> list[ScheduleEntry]:
        """给提示词/UI 的展示视图：被填入层完全覆盖的固定作息段不再重复出现。"""
        merged = self.merged(date)
        result: list[ScheduleEntry] = []
        for entry in merged:
            if entry.source == "routine" and entry.note == CARRIED_NOTE:
                # 骨架每天重读，跨零点那条今天还会出现，副本不必重复。
                recurring = any(
                    x.activity == entry.activity
                    and x.place == entry.place
                    and x.start_min >= entry.end_min
                    for x in merged
                    if x.source == "routine" and x.note != CARRIED_NOTE
                )
                if recurring:
                    continue
            if entry.source == "routine" and entry.note != CARRIED_NOTE:
                covered = any(
                    fill.start_min <= entry.start_min and entry.end_min <= fill.end_min
                    for fill in merged
                    if fill.source != "routine" or fill.note == CARRIED_NOTE
                )
                if covered:
                    continue
            result.append(entry)
        return result

    # ---- 校验与填入 ----

    def fill(
        self,
        entries: list[ScheduleEntry],
        *,
        date: str | None = None,
        start: str = "",
        end: str = "",
        truncate_after: bool = False,
        moment=None,
    ) -> FillResult:
        date = date or get_now_date(moment)
        now_min = now_minutes(moment) if date == get_now_date(moment) else None
        normalized, warnings = self._validate(entries)
        if not normalized:
            return FillResult(
                ok=False,
                message="；".join(warnings) or "没有可用的日程条目",
                warnings=warnings,
            )

        # 昨天跨零点的尾巴已经发生，不许挪动，也不落盘。
        carried = self.carried_entries(date)
        base = self.routine_slots(date)
        carried_index = set(range(len(base), len(base) + len(carried)))
        # 撞上骨架时先把当天的骨架整体挪开/拆段，再落盘填入层。
        slots, normalized, hints, problems = resolve_conflicts(
            list(normalized),
            base + carried,
            now_min,
            frozen_extra=carried_index,
        )
        if problems:
            return FillResult(ok=False, message="；".join(problems))
        warnings.extend(hints)
        # 出门了要安排回家：骨架挪过之后要按新的骨架算空档。
        normalized = self._ensure_return(normalized, Routine(slots=slots), warnings)

        # 覆盖窗口按**最终条目**算：自动补的返程也算在被替换的范围里。
        if start:
            window_start = day_minutes(parse_time(start))
        else:
            window_start = min(entry.start_min for entry in normalized)
        if end:
            window_end = day_minutes(parse_time(end))
        else:
            window_end = max(entry.end_min for entry in normalized)
        if window_end <= window_start:
            window_end += 24 * 60

        if start or end:
            invalid = [
                entry
                for entry in normalized
                if entry.start_min < window_start or entry.end_min > window_end
            ]
            if invalid:
                detail = "、".join(
                    f"{format_time(entry.start)}-{format_time(entry.end)}"
                    for entry in invalid
                )
                return FillResult(
                    ok=False,
                    message=(
                        "这些条目超出了指定范围 "
                        f"{format_time(window_start)}-{format_time(window_end)}：{detail}"
                    ),
                )

        plan = self.load(date)
        kept = self._subtract(list(plan.entries), window_start, window_end)
        # 只有显式 truncate_after 才清掉窗口之后的旧条目。
        if truncate_after:
            kept = [entry for entry in kept if entry.end_min <= window_end]
        plan.entries = sorted(kept + normalized, key=lambda entry: entry.start_min)
        # 填入时把当天的骨架一并定型：之后改 routine.md 不会让已排好的日子错位。
        plan.routine = [x for x in slots if x.note != CARRIED_NOTE]
        self.save(plan)
        self._sync_cross_day(date, normalized)

        return FillResult(
            ok=True,
            plan=plan,
            message=self._describe_changes(normalized, warnings),
            warnings=warnings,
            entries=normalized,
        )

    def _parse_time(self, value: str):
        return parse_time(value)

    def _sync_cross_day(self, date: str, entries: list[ScheduleEntry]) -> None:
        """把跨零点的条目同步到次日：先撤回旧的副本，再按当前内容重写。"""
        next_date = _shift_date(date, 1)
        if not next_date:
            return
        plan = self.load(next_date)
        # 无论这次还有没有跨天条目，上次同步过去的副本都先撤掉。
        self._drop_synced(plan, date)
        overflow = [entry for entry in entries if entry.end_min > 24 * 60]
        if not overflow:
            return
        pieces = []
        for entry in overflow:
            start = max(0, entry.start_min - 24 * 60)
            end = entry.end_min - 24 * 60
            if end <= start:
                continue
            piece = self._clone(
                entry, start=format_time(start), end=format_time(end)
            )
            piece.origin_start = entry.origin_start or format_time(entry.start_min)
            piece.cross_from = date
            pieces.append(piece)
        if not pieces:
            return
        kept = list(plan.entries)
        for piece in pieces:
            # 逐段挖，避免把落在两段之间的无关条目一并删掉。
            kept = self._subtract(kept, piece.start_min, piece.end_min)
        plan.entries = sorted(kept + pieces, key=lambda entry: entry.start_min)
        self.save(plan)

    def _subtract(
        self, entries: list[ScheduleEntry], start: int, end: int
    ) -> list[ScheduleEntry]:
        """从旧填入条目里挖掉 [start, end)，边界处切分。"""
        kept: list[ScheduleEntry] = []
        for entry in entries:
            if not entry.overlaps(start, end):
                kept.append(entry)
                continue
            if entry.start_min < start:
                kept.append(self._clone(entry, end=format_time(start)))
            if entry.end_min > end:
                kept.append(self._clone(entry, start=format_time(end)))
        return kept

    @staticmethod
    def _clone(
        entry: ScheduleEntry, *, start: str = "", end: str = ""
    ) -> ScheduleEntry:
        return ScheduleEntry(
            start=parse_time(start) if start else entry.start,
            end=parse_time(end) if end else entry.end,
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

    def _validate(
        self, entries: list[ScheduleEntry]
    ) -> tuple[list[ScheduleEntry], list[str]]:
        warnings: list[str] = []
        if len(entries) > MAX_FILL_ENTRIES:
            return [], [f"一次最多填入 {MAX_FILL_ENTRIES} 条，收到 {len(entries)} 条"]

        cleaned: list[ScheduleEntry] = []
        for entry in sorted(entries, key=lambda item: item.start_min):
            if entry.activity and len(entry.activity) > MAX_ACTIVITY_CHARS:
                entry.activity = entry.activity[:MAX_ACTIVITY_CHARS]
                warnings.append(
                    f"活动「{entry.activity}」已截断到 {MAX_ACTIVITY_CHARS} 字"
                )
            if entry.start_min < DAY_START_MIN or entry.end_min > DAY_LAST_END_MIN:
                return [], [
                    f"{format_time(entry.start)}-{format_time(entry.end)} "
                    f"超出可填范围 {format_time(DAY_START_MIN)}-{format_time(DAY_LAST_END_MIN)}"
                ]
            if entry.end_min <= entry.start_min:
                return [], [
                    f"{format_time(entry.start)}-{format_time(entry.end)} 的结束时间必须晚于开始"
                ]
            if entry.kind is EntryKind.TRAVEL:
                if not entry.from_place or not entry.to_place:
                    return [], ["travel 必须提供 from / to（从哪到哪）"]
                entry.place = ""
            elif not entry.place:
                entry.place = HOME_PLACE
            entry.source = "fill"
            cleaned.append(entry)

        for left, right in itertools.pairwise(cleaned):
            if right.start_min < left.end_min:
                return [], [
                    f"{format_time(left.start)}-{format_time(left.end)} 与 "
                    f"{format_time(right.start)}-{format_time(right.end)} 重叠"
                ]

        cleaned = self._ensure_travel(cleaned, warnings)
        return cleaned, warnings

    def _ensure_travel(
        self, entries: list[ScheduleEntry], warnings: list[str]
    ) -> list[ScheduleEntry]:
        """相邻 stay 换地点却没有 travel → 在空档里补一段。"""
        result: list[ScheduleEntry] = []
        for entry in entries:
            if result:
                previous = result[-1]
                if (
                    previous.kind is EntryKind.STAY
                    and entry.kind is EntryKind.STAY
                    and previous.place
                    and entry.place
                    and previous.place != entry.place
                ):
                    gap = entry.start_min - previous.end_min
                    if gap < TRAVEL_MIN_MINUTES:
                        warnings.append(
                            f"{previous.place}→{entry.place} 没有时间插入路程，已直接切换"
                        )
                    else:
                        minutes = min(TRAVEL_DEFAULT_MINUTES, gap, TRAVEL_MAX_MINUTES)
                        travel = ScheduleEntry(
                            start=parse_time(format_time(previous.end_min)),
                            end=parse_time(format_time(previous.end_min + minutes)),
                            kind=EntryKind.TRAVEL,
                            activity=f"前往{entry.place}",
                            from_place=previous.place,
                            to_place=entry.place,
                            source="fill",
                            note="auto",
                        )
                        result.append(travel)
                        warnings.append(
                            f"地点从{previous.place}换到{entry.place}，已补 {minutes} 分钟路程"
                        )
                        entry.start = parse_time(format_time(travel.end_min))
            result.append(entry)
        return result

    def _ensure_return(
        self,
        entries: list[ScheduleEntry],
        routine: Routine,
        warnings: list[str],
    ) -> list[ScheduleEntry]:
        """出门了就要有回到家的安排；有空档时自动补一段返程。"""
        if not entries:
            return entries
        last = entries[-1]
        if last.kind is EntryKind.TRAVEL and last.to_place == HOME_PLACE:
            return entries
        if last.kind is EntryKind.STAY and last.place == HOME_PLACE:
            return entries
        from_place = last.to_place if last.kind is EntryKind.TRAVEL else last.place
        last_end = last.end_min
        if not from_place or from_place == HOME_PLACE:
            return entries

        next_home = next(
            (
                slot.start_min
                for slot in routine.slots
                if slot.place == HOME_PLACE and slot.start_min >= last_end
            ),
            DAY_END_MIN,
        )
        gap = next_home - last_end
        if gap < TRAVEL_MIN_MINUTES:
            warnings.append(f"从{from_place}离开后没有时间回家，已直接结束")
            return entries
        minutes = min(TRAVEL_DEFAULT_MINUTES, gap, TRAVEL_MAX_MINUTES)
        entries.append(
            ScheduleEntry(
                start=parse_time(format_time(last_end)),
                end=parse_time(format_time(last_end + minutes)),
                kind=EntryKind.TRAVEL,
                activity="回家",
                from_place=from_place,
                to_place=HOME_PLACE,
                source="fill",
                note="auto",
            )
        )
        warnings.append(f"从{from_place}离开后没有返程，已补 {minutes} 分钟回家")
        return entries

    def _describe_changes(
        self, entries: list[ScheduleEntry], warnings: list[str]
    ) -> str:
        lines = ["日程已更新："]
        for entry in entries:
            if entry.kind is EntryKind.TRAVEL:
                lines.append(
                    f"- {entry.shown_start}-{format_time(entry.end)} "
                    f"从{entry.from_place}前往{entry.to_place}"
                )
            else:
                lines.append(
                    f"- {entry.shown_start}-{format_time(entry.end)} "
                    f"在{entry.place}：{entry.activity or '（无描述）'}"
                )
        lines.extend(f"- 注意：{item}" for item in warnings)
        return "\n".join(lines)

    # ---- 位置 ----

    def locate(self, date: str | None = None, moment=None) -> LocationState:
        date = date or get_now_date(moment)
        minute = now_minutes(moment)
        plan = self.load(date)
        # 填入层优先：`go` 允许临时压过固定作息。
        for entry in plan.entries:
            if entry.contains(minute):
                return self._state_from_entry(entry, minute)
        # 昨天跨过零点的尾巴也参与定位。
        carried = self.carried_entries(date)
        slot = next((x for x in carried if x.contains(minute)), None)
        slots = plan.routine or self.routine().slots
        if slot is None:
            slot = next((x for x in slots if x.contains(minute)), None)
        if slot is not None:
            return self._state_from_entry(slot, minute)
        # 空档期也给结束时间。
        upcoming = sorted(
            x.start_min
            for x in list(slots) + carried
            if x.start_min > minute
        )
        until = format_time(upcoming[0]) if upcoming else ""
        return LocationState(
            place=HOME_PLACE, activity="空闲", since=format_time(minute), until=until
        )

    @staticmethod
    def _state_from_entry(entry: ScheduleEntry, minute: int) -> LocationState:
        if entry.kind is EntryKind.TRAVEL:
            return LocationState(
                place=f"路上（{entry.from_place} → {entry.to_place}）",
                activity=entry.activity or "路上",
                moving=True,
                from_place=entry.from_place,
                to_place=entry.to_place,
                since=format_time(entry.start_min),
                until=format_time(entry.end_min),
                eta_minutes=entry.end_min - minute,
                source=entry.source,
            )
        return LocationState(
            place=entry.place or HOME_PLACE,
            activity=entry.activity or "空闲",
            since=format_time(entry.start_min),
            until=format_time(entry.end_min),
            source=entry.source,
        )

    def go(self, place: str, minutes: int = 0, *, moment=None) -> FillResult:
        """立即前往：截断当前段，插入 travel + 目的地 stay。"""
        date = get_now_date(moment)
        place = str(place or "").strip() or HOME_PLACE
        minute = now_minutes(moment)
        current = self.locate(date, moment)
        end_min = (
            day_minutes(parse_time(current.until)) if current.until else DAY_END_MIN
        )
        if end_min <= minute:
            end_min = DAY_END_MIN
        try:
            travel_minutes = int(minutes or 0)
        except (TypeError, ValueError):
            travel_minutes = 0
        if travel_minutes <= 0:
            travel_minutes = TRAVEL_DEFAULT_MINUTES
        travel_minutes = min(
            max(TRAVEL_MIN_MINUTES, travel_minutes), TRAVEL_MAX_MINUTES
        )
        arrive = min(minute + travel_minutes, end_min)
        if arrive <= minute:
            return FillResult(ok=False, message="现在没有可以出发的时间段")
        if place == current.place:
            return FillResult(ok=False, message=f"你已经在{place}了")

        new_entries = [
            ScheduleEntry(
                start=parse_time(format_time(minute)),
                end=parse_time(format_time(arrive)),
                kind=EntryKind.TRAVEL,
                activity=f"前往{place}",
                from_place=current.place,
                to_place=place,
                source="go",
            )
        ]
        if arrive < end_min:
            new_entries.append(
                ScheduleEntry(
                    start=parse_time(format_time(arrive)),
                    end=parse_time(format_time(end_min)),
                    kind=EntryKind.STAY,
                    place=place,
                    activity=f"在{place}",
                    source="go",
                )
            )

        plan = self.load(date)
        plan.entries = self._subtract(plan.entries, minute, end_min) + new_entries
        plan.entries.sort(key=lambda entry: entry.start_min)
        self.save(plan)
        state = self.locate(date, moment)
        return FillResult(
            ok=True,
            plan=plan,
            message=state.describe(),
            entries=new_entries,
        )


def describe_day(entries: list[ScheduleEntry], limit: int = 800) -> str:
    """紧凑日程文本；超预算保留当前之后的前 3 段。

    只有确实还有条目没输出时才追加「用 plan(action="show")」提示，
    避免刚好卡在预算边界时给出误导性的省略号。
    """
    lines: list[str] = []
    used = 0
    truncated = False
    for index, entry in enumerate(entries):
        if entry.kind is EntryKind.TRAVEL:
            text = f"{entry.shown_start}-{format_time(entry.end)} 前往{entry.to_place}"
        else:
            text = (
                f"{entry.shown_start}-{format_time(entry.end)} "
                f"{entry.place}：{entry.activity or '空闲'}"
            )
        if used + len(text) + 1 > limit and lines:
            truncated = index < len(entries)
            break
        lines.append(text)
        used += len(text) + 1
    if truncated:
        lines.append('……（完整日程用 plan(action="show") 查看）')
    return "\n".join(lines)
