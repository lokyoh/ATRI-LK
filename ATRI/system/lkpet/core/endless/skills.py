"""无尽模式的技能：160 个定义、4 槽、强化。

32 原型 × 5 等阶：8 元素 × 4 系列（主力伤害 / 特色伤害 / 辅助 / 防御-恢复）。
同原型跨阶只改名字与数值，**机制不变**——学会「风刃」的用法后换高阶不用重新理解。

转伤属性：风=速 土=防 光=蓝 暗=血，其余用攻。
这就是「堆防御也有输出手段」的实现。
"""

from __future__ import annotations

import random
from typing import Any

SERIES = ("main", "special", "support", "guard")

SERIES_CN = {
    "main": "主力伤害",
    "special": "特色伤害",
    "support": "辅助",
    "guard": "防御/恢复",
}

ELEMENTS = ("normal", "fire", "ice", "thunder", "wind", "earth", "light", "dark")

ELEMENT_CN = {
    "normal": "物理",
    "fire": "火",
    "ice": "冰",
    "thunder": "雷",
    "wind": "风",
    "earth": "土",
    "light": "光",
    "dark": "暗",
}

DIR_ATTR = {
    "normal": "atk",
    "fire": "atk",
    "ice": "atk",
    "thunder": "atk",
    "wind": "spd",
    "earth": "def",
    "light": "mp",
    "dark": "hp",
}
"""元素 → 转伤属性：非攻击属性也有输出手段。"""

ENHANCE_MAX = 10
ENHANCE_STEP = 0.15
"""强化上限与每级增幅（满 10 次 = ×2.5）。"""


def load_skills_config() -> dict[str, Any]:
    from ...config_loader import load_config

    return load_config("endless_skills")


def skill_list(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    cfg = cfg or load_skills_config()
    return [dict(s) for s in (cfg.get("skills") or [])]


def skill_specs(cfg: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    return {str(s["id"]): s for s in skill_list(cfg)}


def active_slots(cfg: dict[str, Any] | None = None) -> int:
    cfg = cfg or load_skills_config()
    return max(1, int((cfg.get("limits") or {}).get("active_slots", 4) or 4))


def available_tiers(stage: int) -> int:
    """该阶段可出的最高技能等阶：每大阶段 +1（与装备同步）。"""
    major = (max(1, int(stage)) - 1) // 5 + 1
    return min(5, major)


def skills_for_stage(
    stage: int, cfg: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """该阶段可获得的技能（等阶不超过当前上限）。"""
    cap = available_tiers(stage)
    return [s for s in skill_list(cfg) if int(s.get("tier", 0)) <= cap]


def offer_skills(
    rng: random.Random,
    stage: int,
    known: list[str] | None = None,
    count: int = 4,
    cfg: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """抽 count 个**尚未获得**的技能（等阶不超过当前上限）。

    可提供不足 count 个时，有几个给几个——这是「技能全部获得后停止提供」的实现。
    """
    taken = {str(k) for k in (known or [])}
    pool = [s for s in skills_for_stage(stage, cfg) if str(s["id"]) not in taken]
    if not pool:
        return []
    rng.shuffle(pool)
    return pool[:count]


SKILL_PRICE = {1: 40, 2: 80, 3: 120, 4: 160, 5: 200}
"""技能售价（文档 §9.8）：整体比遗物贵一档。"""


def price_of_skill(spec: dict[str, Any]) -> int:
    """技能价格：按等阶查表。"""
    return int(SKILL_PRICE.get(int(spec.get("tier", 1) or 1), SKILL_PRICE[1]))


def enhance_multiplier(times: int) -> float:
    """强化次数 → 威力乘区（0 次 = 1.0，10 次 = 2.5）。"""
    n = max(0, min(ENHANCE_MAX, int(times)))
    return round(1.0 + ENHANCE_STEP * n, 4)


def enhance_cost(tier: int, times: int) -> int:
    """第 times+1 次强化的价格：越高级技能越贵。

    base = 30 + 10 * max(0, int(tier) - 1)   # T1 30, T5 70
    每次再递增 10
    """
    base = 30 + 10 * max(0, int(tier) - 1)
    return base + 10 * max(0, int(times))


def total_enhance_cost(tier: int, times: int) -> int:
    """从 0 强化到 times 次的累计花费。"""
    return sum(enhance_cost(tier, i) for i in range(max(0, int(times))))


def skill_power(spec: dict[str, Any], times: int = 0) -> int:
    """最终威力 = 档位系数（已含在 power 里）+ 强化乘区。"""
    base = int(spec.get("power", 0) or 0)
    return max(0, round(base * enhance_multiplier(times)))


def validate_equipped(
    equipped: list[str] | None,
    cfg: dict[str, Any] | None = None,
) -> list[str]:
    """技能槽表规整：只留存在的 id，且不超过槽位上限。"""
    specs = skill_specs(cfg)
    out: list[str] = []
    for skill_id in list(equipped or []):
        if str(skill_id) in specs and len(out) < active_slots(cfg):
            out.append(str(skill_id))
    return out


def equip_skill(
    equipped: list[str] | None,
    skill_id: str,
    cfg: dict[str, Any] | None = None,
) -> tuple[list[str], str | None]:
    """装一个技能；返回 ``(新表, 被换下的 id 或 None)``。

    槽满就换掉第一个——换下的进「已学列表」，强化进度跟着技能走。
    """
    specs = skill_specs(cfg)
    current = validate_equipped(equipped, cfg)
    if str(skill_id) not in specs:
        return current, None
    replaced: str | None = None
    if len(current) >= active_slots(cfg):
        replaced = current.pop(0)
    if str(skill_id) in current:
        return current, replaced
    current.append(str(skill_id))
    return current, replaced
