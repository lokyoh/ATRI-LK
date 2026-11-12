"""着装：白天 / 晚上两套，由 `ATRI_OUTFIT` 生成。"""

from __future__ import annotations

from pathlib import Path

from ATRI.log import log

from ...llm import ModelType, llm_manager
from .model import OutfitPlan, get_now_date
from .store import read_json, write_json

MAX_OUTFIT_CHARS = 120


class OutfitStore:
    def __init__(self, root: Path):
        self.root = root
        self.outfit_dir = root / "outfit"

    def path_for(self, date: str) -> Path:
        return self.outfit_dir / f"{date}.json"

    def load(self, date: str | None = None) -> OutfitPlan:
        date = date or get_now_date()
        data = read_json(self.path_for(date))
        if not data:
            return OutfitPlan(date=date)
        try:
            return OutfitPlan.from_dict(data)
        except (TypeError, ValueError) as exc:
            log.warning(f"穿搭文件 {date} 解析失败，按空数据：{exc}")
            return OutfitPlan(date=date)

    def save(self, plan: OutfitPlan) -> None:
        plan.updated_at = _now_ts()
        write_json(self.path_for(plan.date), plan.to_dict())

    def set_period(self, period: str, text: str, date: str | None = None) -> OutfitPlan:
        plan = self.load(date)
        text = str(text or "").strip()[:MAX_OUTFIT_CHARS]
        if period == "night":
            plan.night = text
        else:
            plan.day = text
        self.save(plan)
        return plan

    def describe(
        self,
        date: str | None = None,
        hour: int | None = None,
        night_hour: int | None = None,
    ) -> str:
        plan = self.load(date)
        hour = _now_hour() if hour is None else hour
        if not plan.day and not plan.night:
            return "今天还没决定穿什么"
        night_hour = _configured_night_hour() if night_hour is None else night_hour
        day = plan.day or "（白天穿搭未记录）"
        night = plan.night or "（晚上穿搭未记录）"
        current = plan.current_text(hour, night_hour)
        lines = [f"白天：{day}", f"晚上：{night}", f"现在穿：{current}"]
        return "\n".join(lines)

    def prompt_text(
        self,
        date: str | None = None,
        hour: int | None = None,
        night_hour: int | None = None,
    ) -> str:
        plan = self.load(date)
        if not plan.day and not plan.night:
            return "今天还没决定穿什么"
        if night_hour is None:
            night_hour = _configured_night_hour()
        current = plan.current_text(_now_hour() if hour is None else hour, night_hour)
        return (
            f"白天：{plan.day or '（未记录）'}\n"
            f"晚上：{plan.night or '（未记录）'}\n"
            f"现在穿：{current}"
        )

    async def generate(
        self,
        *,
        date: str | None = None,
        activities: str = "",
        place: str = "",
    ) -> tuple[bool, str]:
        """调 `ATRI_OUTFIT`；缺模型/失败只降级，不抛异常。"""
        from .home import HomeStore

        date = date or get_now_date()
        if not llm_manager.has_type(ModelType.ATRI_OUTFIT):
            return False, "未配置 atri-outfit 类型模型，保留现有穿搭"
        import datetime

        from ...agent.role import ATRI
        from .model import _now_local

        previous = self.load(
            (_now_local() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        )
        home = HomeStore(self.root).summary()
        prompt = (
            "为亚托莉生成今天的白天与晚上两套穿搭。\n"
            f"角色：{ATRI.get_role_prompt()}\n"
            f"家的衣柜：{home or '（无记录）'}\n"
            f"今天的活动与地点：{activities or place or '（尚未安排）'}\n"
            f"昨天的穿搭（避免重复）：白天 {previous.day or '无'}；晚上 {previous.night or '无'}\n"
            '只输出 JSON：{"day": "...", "night": "..."}。\n'
            "day 是白天外穿或居家服，night 是晚上回家后的居家服/睡衣；"
            f"每段不超过 {MAX_OUTFIT_CHARS} 字，不要 Markdown。"
        )
        try:
            response = await llm_manager.call_agent(
                ModelType.ATRI_OUTFIT, prompt, "Agent-今日穿搭"
            )
            data = _extract_json(response.content)
        except Exception as exc:
            log.warning(f"穿搭生成失败，保留现有穿搭：{exc}")
            return False, "穿搭生成失败，保留现有穿搭"
        day = str(data.get("day") or "").strip()[:MAX_OUTFIT_CHARS]
        night = str(data.get("night") or "").strip()[:MAX_OUTFIT_CHARS]
        if not day or not night:
            return False, "穿搭模型没有给出完整的白天/晚上两套，保留现有穿搭"
        plan = OutfitPlan(date=date, day=day, night=night, updated_at=_now_ts())
        self.save(plan)
        return True, f"穿搭已更新：\n白天：{day}\n晚上：{night}"


def _extract_json(text: str) -> dict:
    import json
    import re

    body = str(text or "")
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", body, re.DOTALL)
    if match:
        body = match.group(1)
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        cleaned = re.sub(r",\s*([}\]])", r"\1", body)
        return json.loads(cleaned)


def _now_ts() -> float:
    from ATRI.utils.datetime import now

    return now().timestamp()


def _now_hour() -> int:
    from ATRI.utils.datetime import now

    return now().hour


def _configured_night_hour() -> int:
    """读 ``outfit_night_hour``；读不到时退回 20。

    不写死默认值：配置改成 22 点时，``describe()`` 与提示词必须用同一个
    切换点，否则她自己看到的「现在穿」和 WebUI 显示的不一致。
    """
    try:
        from ...config import config

        return max(0, min(23, int(config.get("outfit_night_hour") or 20)))
    except Exception:
        return 20
