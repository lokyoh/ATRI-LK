"""生活系统的数据模型与时间工具。

一天的口径固定为 **06:00 到次日 06:00**：凌晨 0-6 点归前一天，
这样「02:00-06:00 睡觉」仍然是同一天日程的尾巴，而不是第二天开头。
"""

from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

#: 当天的时间窗口（分钟，从 00:00 起算；凌晨按 +24h 归入前一天）
DAY_START_MIN = 6 * 60
DAY_END_MIN = 30 * 60

#: 固定作息允许的最大条数
MAX_ROUTINE_SLOTS = 32

#: 单次填入允许的最大条数
MAX_FILL_ENTRIES = 12

#: 单条活动文本上限
MAX_ACTIVITY_CHARS = 60

#: 默认「家」的地点名
HOME_PLACE = "家"


class EntryKind(StrEnum):
    STAY = "stay"
    """待在某地"""

    TRAVEL = "travel"
    """从 A 到 B 的路上"""


def clock_minutes(value: datetime.time) -> int:
    return int(value.hour) * 60 + int(value.minute)


def day_minutes(value: datetime.time) -> int:
    """把钟点映射到当天窗口；凌晨 0-6 点算作 24 点以后。"""
    minutes = clock_minutes(value)
    return minutes + 24 * 60 if minutes < DAY_START_MIN else minutes


def parse_time(value: Any) -> datetime.time:
    """接受 ``time`` / ``"09:00"`` / ``"9:00"``；非法值抛 ValueError。"""
    if isinstance(value, datetime.time):
        return value.replace(second=0, microsecond=0)
    if isinstance(value, datetime.datetime):
        return value.time().replace(second=0, microsecond=0)
    text = str(value or "").strip()
    match = re.match(r"^(\d{1,2}):(\d{2})$", text)
    if not match:
        raise ValueError(f"时间格式应为 HH:MM，收到 {value!r}")
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"时间超出范围：{value!r}")
    return datetime.time(hour=hour, minute=minute)


def format_time(value: datetime.time | int) -> str:
    """分钟或 time → ``HH:MM``（分钟超过 24h 时取当日钟点）。"""
    if isinstance(value, int):
        minutes = value % (24 * 60)
        return f"{minutes // 60:02d}:{minutes % 60:02d}"
    return f"{value.hour:02d}:{value.minute:02d}"


def _now_local() -> datetime.datetime:
    """项目统一时区的当前时间。"""
    try:
        from ATRI.utils.datetime import now as atri_now

        return atri_now()
    except Exception:
        return datetime.datetime.now()


def get_now_date(now: datetime.datetime | None = None) -> str:
    """06:00 前仍算前一天，与旧 `ATRISchedule.get_now_date()` 一致。"""
    current = now or _now_local()
    if current.hour < 6:
        current -= datetime.timedelta(days=1)
    return current.strftime("%Y-%m-%d")


def now_minutes(now: datetime.datetime | None = None) -> int:
    """当前分钟在「当天窗口」里的位置。"""
    current = now or _now_local()
    minutes = current.hour * 60 + current.minute
    if minutes < DAY_START_MIN:
        minutes += 24 * 60
    return minutes


@dataclass
class ScheduleEntry:
    """一条日程：待在某地，或正在路上。"""

    start: datetime.time
    end: datetime.time
    kind: EntryKind = EntryKind.STAY
    activity: str = ""
    place: str = ""
    from_place: str = ""
    to_place: str = ""
    source: str = "fill"
    note: str = ""

    @property
    def start_min(self) -> int:
        return day_minutes(self.start)

    @property
    def end_min(self) -> int:
        end = day_minutes(self.end)
        if end <= self.start_min:
            end += 24 * 60
        return end

    @property
    def duration(self) -> int:
        return max(0, self.end_min - self.start_min)

    def contains(self, minute: int) -> bool:
        return self.start_min <= minute < self.end_min

    def overlaps(self, start: int, end: int) -> bool:
        return self.start_min < end and start < self.end_min

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "start": format_time(self.start),
            "end": format_time(self.end),
            "kind": self.kind.value,
            "activity": self.activity,
            "source": self.source,
        }
        if self.place:
            payload["place"] = self.place
        if self.from_place:
            payload["from"] = self.from_place
        if self.to_place:
            payload["to"] = self.to_place
        if self.note:
            payload["note"] = self.note
        return payload

    @classmethod
    def from_dict(cls, data: dict) -> ScheduleEntry:
        start = parse_time(data.get("start") or data.get("start_time"))
        end = parse_time(data.get("end") or data.get("end_time"))
        kind = EntryKind(str(data.get("kind") or "stay"))
        activity = str(data.get("activity") or data.get("schedule") or "").strip()
        return cls(
            start=start,
            end=end,
            kind=kind,
            activity=activity,
            place=str(data.get("place") or "").strip(),
            from_place=str(data.get("from") or data.get("from_place") or "").strip(),
            to_place=str(data.get("to") or data.get("to_place") or "").strip(),
            source=str(data.get("source") or "fill"),
            note=str(data.get("note") or ""),
        )


@dataclass
class DayPlan:
    """某一天的填入层；固定作息不写进来，读取时实时合并。"""

    date: str
    entries: list[ScheduleEntry] = field(default_factory=list)
    version: int = 1
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "version": self.version,
            "updated_at": self.updated_at,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: dict) -> DayPlan:
        return cls(
            date=str(data.get("date") or ""),
            version=int(data.get("version") or 1),
            updated_at=float(data.get("updated_at") or 0.0),
            entries=[
                ScheduleEntry.from_dict(item)
                for item in (data.get("entries") or [])
                if isinstance(item, dict)
            ],
        )


@dataclass
class LocationState:
    """由「固定作息 + 填入层」实时派生，不依赖上次写入。"""

    place: str = HOME_PLACE
    activity: str = "空闲"
    moving: bool = False
    from_place: str = ""
    to_place: str = ""
    since: str = ""
    until: str = ""
    eta_minutes: int = 0
    source: str = ""

    def describe(self) -> str:
        if self.moving:
            eta = max(0, int(self.eta_minutes))
            return f"你正在从{self.from_place}前往{self.to_place}的路上，大约 {eta} 分钟后到。"
        place = self.place or "地点未记录"
        activity = self.activity or "空闲"
        line = f"你在{place}，{activity}"
        if self.until:
            line += f"，预计 {self.until} 离开"
        return line + "。"


@dataclass
class OutfitPlan:
    date: str
    day: str = ""
    night: str = ""
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "day": self.day,
            "night": self.night,
            "updated_at": self.updated_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: dict) -> OutfitPlan:
        return cls(
            date=str(data.get("date") or ""),
            day=str(data.get("day") or "").strip(),
            night=str(data.get("night") or "").strip(),
            updated_at=float(data.get("updated_at") or 0.0),
        )

    def current_text(self, hour: int, night_hour: int = 20) -> str:
        """20:00 以后与凌晨睡眠时段穿晚上那套。"""
        night = hour >= night_hour or hour < 6
        text = self.night if night else self.day
        return text or "今天还没决定穿什么"
