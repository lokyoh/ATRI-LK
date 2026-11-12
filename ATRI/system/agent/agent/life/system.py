"""生活系统 facade：日程、位置、穿搭、家的统一入口。"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from ATRI.log import log

from ...config import config
from ...service import plugin
from .home import HomeStore
from .model import (
    DayPlan,
    EntryKind,
    LocationState,
    OutfitPlan,
    ScheduleEntry,
    format_time,
    get_now_date,
)
from .outfit import OutfitStore
from .schedule_store import FillResult, ScheduleStore, describe_day
from .store import prune_dated_files

SCHEDULE_SUMMARY_BUDGET = 800


class LifeSystem:
    def __init__(self, root: Path | None = None):
        self.root = root or (plugin.get_path() / "life")
        self.user_dir = self.root / "user"
        # 固定作息是「用户可改」的：放在 life/routine.md（用户目录），
        # 与随仓库发布的 res/data/agent/life/routine.md 区分。
        self.schedules = ScheduleStore(self.root, user_dir=self.root)
        self.outfits = OutfitStore(self.root)
        self.homes = HomeStore(self.root)

    # ---- 读取 ----

    def routine(self):
        return self.schedules.routine()

    def plan(self, date: str | None = None) -> list[ScheduleEntry]:
        return self.schedules.display_entries(date)

    def location(self, date: str | None = None, moment=None) -> LocationState:
        return self.schedules.locate(date, moment)

    def outfit(self, date: str | None = None, hour: int | None = None) -> OutfitPlan:
        return self.outfits.load(date)

    def home_summary(self) -> str:
        return self.homes.summary()

    def schedule_summary(
        self, date: str | None = None, limit: int = SCHEDULE_SUMMARY_BUDGET
    ) -> str:
        return describe_day(self.plan(date), limit=limit)

    def prompt_parts(self, date: str | None = None) -> dict[str, str]:
        """提示词需要的四段：位置、安排、穿搭、家。"""
        date = date or get_now_date()
        night_hour = int(config.get("outfit_night_hour") or 20)
        budget = int(config.get("schedule_summary_budget") or SCHEDULE_SUMMARY_BUDGET)
        state = self.location(date)
        return {
            "location": state.describe(),
            "schedule": self.schedule_summary(date, limit=budget),
            "outfit": self.outfits.prompt_text(date, night_hour=night_hour),
            "home": self.home_summary(),
        }

    # ---- 日程写入 ----

    def fill_schedule(
        self,
        entries: list[ScheduleEntry],
        *,
        date: str | None = None,
        start: str = "",
        end: str = "",
        truncate_after: bool = False,
        force: bool = False,
    ) -> FillResult:
        from ...agent.telemetry import telemetry

        started = time.perf_counter()
        result = self.schedules.fill(
            entries,
            date=date,
            start=start,
            end=end,
            truncate_after=truncate_after,
            force=force,
        )
        telemetry.life(
            action="plan",
            ok=result.ok,
            duration=time.perf_counter() - started,
            count=len(result.entries),
            detail=result.message[:200],
        )
        return result

    def parse_entries(self, payload) -> tuple[list[ScheduleEntry], list[str]]:
        """把模型 JSON / 调参字典转成条目；格式问题在这里一次收齐。"""
        issues: list[str] = []
        raw_items = payload.get("entries") if isinstance(payload, dict) else payload
        if not isinstance(raw_items, list):
            return [], ["entries 必须是数组"]
        entries: list[ScheduleEntry] = []
        for index, item in enumerate(raw_items, 1):
            if not isinstance(item, dict):
                issues.append(f"第 {index} 条不是对象")
                continue
            try:
                entries.append(ScheduleEntry.from_dict(item))
            except (TypeError, ValueError) as exc:
                issues.append(f"第 {index} 条：{exc}")
        return entries, issues

    def go(self, place: str, minutes: int = 0, *, moment=None) -> FillResult:
        from ...agent.telemetry import telemetry

        started = time.perf_counter()
        result = self.schedules.go(place, minutes, moment=moment)
        telemetry.life(
            action="go",
            ok=result.ok,
            duration=time.perf_counter() - started,
            count=len(result.entries),
            detail=result.message[:200],
        )
        return result

    def clear(self, date: str | None = None) -> bool:
        return self.schedules.clear(date or get_now_date())

    def save_plan(self, plan: DayPlan) -> None:
        self.schedules.save(plan)

    # ---- 穿搭 ----

    def set_outfit(self, period: str, text: str, date: str | None = None) -> OutfitPlan:
        return self.outfits.set_period(period, text, date)

    async def generate_outfit(
        self, *, date: str | None = None, activities: str = "", place: str = ""
    ) -> tuple[bool, str]:
        from ...agent.telemetry import telemetry

        started = time.perf_counter()
        ok, message = await self.outfits.generate(
            date=date, activities=activities, place=place
        )
        telemetry.life(
            action="outfit",
            ok=ok,
            duration=time.perf_counter() - started,
            detail=message[:200],
        )
        return ok, message

    # ---- 家 ----

    def home(self) -> HomeStore:
        return self.homes

    # ---- 初始化 / 清理 ----

    def ensure(self) -> None:
        """首次启动：确保 home.md 存在，清理过期文件。"""
        self.homes.ensure()
        removed = prune_dated_files(self.schedules.schedule_dir) + prune_dated_files(
            self.outfits.outfit_dir
        )
        if removed:
            log.info(f"清理了 {removed} 个过期生活数据文件")

    def archive(self) -> None:
        """按日文件自动清理；旧数据保留 7 天。"""
        prune_dated_files(self.schedules.schedule_dir)
        prune_dated_files(self.outfits.outfit_dir)


_life_system: LifeSystem | None = None


def get_life_system() -> LifeSystem:
    global _life_system
    root = plugin.get_path() / "life"
    if _life_system is None or _life_system.root != root:
        _life_system = LifeSystem(root)
    return _life_system


# ---- 解析模型 JSON ----


def extract_json(text: str) -> dict:
    """从模型输出里提取 JSON（容忍代码块与尾随逗号）。"""
    body = str(text or "")
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", body, re.DOTALL)
    if match:
        body = match.group(1)
    body = body.strip()
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        cleaned = re.sub(r",\s*([}\]])", r"\1", body)
        return json.loads(cleaned)


def describe_state(state: LocationState) -> str:
    return state.describe()


def describe_entries(entries: list[ScheduleEntry]) -> str:
    lines = []
    for entry in entries:
        if entry.kind is EntryKind.TRAVEL:
            lines.append(
                f"{format_time(entry.start)}-{format_time(entry.end)} "
                f"从{entry.from_place}前往{entry.to_place}"
            )
        else:
            lines.append(
                f"{format_time(entry.start)}-{format_time(entry.end)} "
                f"在{entry.place}：{entry.activity or '空闲'}"
            )
    return "\n".join(lines)
