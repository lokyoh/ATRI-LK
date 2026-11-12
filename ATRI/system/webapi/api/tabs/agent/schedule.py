"""生活系统 API：日程、位置、穿搭、家。"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ATRI.exceptions import str_traceback
from ATRI.log import log
from ATRI.system.agent.agent.life import (
    EntryKind,
    get_life_system,
    get_now_date,
    parse_time,
)

from ....base_model import Result
from ....utils import authentication

router = APIRouter()


class ScheduleEntryInfo(BaseModel):
    start: str
    end: str
    kind: str
    activity: str
    place: str = ""
    from_place: str = ""
    to_place: str = ""
    source: str = ""
    note: str = ""


class LocationInfo(BaseModel):
    place: str
    activity: str
    moving: bool
    from_place: str = ""
    to_place: str = ""
    since: str = ""
    until: str = ""
    eta_minutes: int = 0
    text: str
    """渲染后的自然语言（与 where() 一致）"""


class OutfitInfo(BaseModel):
    date: str
    day: str
    night: str
    current: str
    """当前时段实际穿的那套"""


def _now_hour() -> int:
    from ATRI.utils.datetime import now

    return now().hour


class HomeInfo(BaseModel):
    summary: str
    """位置 + 房间 + 周边（提示词用的摘要）"""
    content: str
    """完整 home.md"""


def _night_hour() -> int:
    """与提示词一致的晚间穿搭切换点。

    ``OutfitPlan.current_text`` 的默认值是 20，而提示词读 ``outfit_night_hour``；
    配置改成 22 时，WebUI 显示的「现在穿」会和她自己认为的不一致。
    """
    from ATRI.system.agent.config import config

    try:
        return max(0, min(23, int(config.get("outfit_night_hour") or 20)))
    except Exception:
        return 20


class LifeSnapshot(BaseModel):
    date: str
    location: LocationInfo
    schedule: list[ScheduleEntryInfo]
    outfit: OutfitInfo
    home: HomeInfo


class SetOutfitModel(BaseModel):
    period: str = "day"
    """day / night"""
    text: str


class HomeEditModel(BaseModel):
    action: str = "show"
    """show / edit / add / generate"""
    section: str = ""
    content: str = ""


def _entry_info(entry) -> ScheduleEntryInfo:
    return ScheduleEntryInfo(
        start=entry.start.strftime("%H:%M"),
        end=entry.end.strftime("%H:%M"),
        kind=entry.kind.value,
        activity=entry.activity,
        place=entry.place,
        from_place=entry.from_place,
        to_place=entry.to_place,
        source=entry.source,
        note=entry.note,
    )


def _snapshot(date: str) -> LifeSnapshot:
    life = get_life_system()
    state = life.location(date)
    outfit = life.outfit(date)
    home = life.home()
    return LifeSnapshot(
        date=date,
        location=LocationInfo(
            place=state.place,
            activity=state.activity,
            moving=state.moving,
            from_place=state.from_place,
            to_place=state.to_place,
            since=state.since,
            until=state.until,
            eta_minutes=state.eta_minutes,
            text=state.describe(),
        ),
        schedule=[_entry_info(entry) for entry in life.plan(date)],
        outfit=OutfitInfo(
            date=outfit.date,
            day=outfit.day,
            night=outfit.night,
            current=outfit.current_text(_now_hour(), night_hour=_night_hour()),
        ),
        home=HomeInfo(summary=life.home_summary(), content=home.read()),
    )


@router.get(
    "/get_life",
    dependencies=[authentication()],
    response_model=Result[LifeSnapshot],
    response_class=JSONResponse,
    description="获取某天的日程、位置、穿搭与家",
)
async def _(date: str = "") -> Result[LifeSnapshot]:
    try:
        return Result.ok(_snapshot(date or get_now_date()), "拿到生活信息啦!")
    except Exception as e:
        log.error(f"{router.prefix}/get_life 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/set_outfit",
    dependencies=[authentication()],
    response_model=Result[OutfitInfo],
    response_class=JSONResponse,
    description="手动指定白天/晚上穿搭",
)
async def _(payload: SetOutfitModel) -> Result[OutfitInfo]:
    try:
        period = payload.period.strip().lower()
        if period not in {"day", "night"}:
            return Result.fail("period 只能是 day 或 night", 400)
        text = payload.text.strip()
        if not text:
            return Result.fail("text 不能为空", 400)
        life = get_life_system()
        plan = life.set_outfit(period, text)
        return Result.ok(
            OutfitInfo(
                date=plan.date,
                day=plan.day,
                night=plan.night,
                current=plan.current_text(_now_hour()),
            ),
            "设置成功!",
        )
    except Exception as e:
        log.error(f"{router.prefix}/set_outfit 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/set_home",
    dependencies=[authentication()],
    response_model=Result[HomeInfo],
    response_class=JSONResponse,
    description="查看 / 修改 / 生成家的内容（与 agent 的 home() 同语义）",
)
async def _(payload: HomeEditModel) -> Result[HomeInfo]:
    try:
        life = get_life_system()
        home = life.home()
        action = payload.action.strip().lower()
        if action == "show":
            text = home.show(payload.section)
            return Result.ok(
                HomeInfo(summary=life.home_summary(), content=text), "拿到信息啦!"
            )
        if action == "edit":
            if not payload.section or not payload.content:
                return Result.fail("edit 需要 section 与 content", 400)
            message = home.edit(payload.section, payload.content)
        elif action == "add":
            if not payload.section or not payload.content:
                return Result.fail("add 需要 section 与 content", 400)
            message = home.add(payload.section, payload.content)
        elif action == "generate":
            ok, message = await home.generate()
            if not ok:
                return Result.fail(message, 400)
        else:
            return Result.fail(f"未知的 action：{action}", 400)
        if message.startswith("家里没有") or message.startswith("写入失败"):
            return Result.fail(message, 400)
        return Result.ok(
            HomeInfo(summary=life.home_summary(), content=home.read()), message
        )
    except Exception as e:
        log.error(f"{router.prefix}/set_home 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/clear_life_plan",
    dependencies=[authentication()],
    response_model=Result[LifeSnapshot],
    response_class=JSONResponse,
    description="清空某天填入的活动（固定作息保留）",
)
async def _(date: str = "") -> Result[LifeSnapshot]:
    try:
        target = date or get_now_date()
        get_life_system().clear(target)
        return Result.ok(_snapshot(target), "已清空填入的日程")
    except Exception as e:
        log.error(f"{router.prefix}/clear_life_plan 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


def parse_entry_kind(value: str) -> EntryKind:
    try:
        return EntryKind(str(value))
    except ValueError:
        return EntryKind.STAY


__all__ = ["parse_entry_kind", "parse_time", "router"]
