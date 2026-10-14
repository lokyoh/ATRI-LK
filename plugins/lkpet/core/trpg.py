"""跑团属性与 d100 检定（任务书 §13.1/§13.2）。

六维（STR/CON/DEX/POW/INT/APP）与百分比技能构成跑团卡，存 ``PETTRPG``，
与养成五维互不覆盖、互不换算。所有数值规则外置 trpg.yaml；本模块只负责掷骰、
档位判定与成长，剧情推进在 ``core/trpg_gm.py``。
"""

import random
from datetime import datetime
from typing import Any

CHECK_ORDER = ("STR", "CON", "DEX", "POW", "INT", "APP")
"""六维键序（展示与建卡顺序，与 trpg.yaml 的 attributes.order 一致）。"""


def _load_trpg_config() -> dict[str, Any]:
    from ..config_loader import load_config

    return load_config("trpg")


def _text(key: str, default: str = "", **fields: Any) -> str:
    from ..utils.texts import text

    return text(f"trpg.{key}", default, **fields)


# ---------------------------------------------------------------------------
# 跑团卡
# ---------------------------------------------------------------------------

async def get_or_create_card(player_id: str | int):
    """取回跑团卡行；不存在时按默认值建行（属性仍为空，等建卡掷骰）。"""
    from ..database import Trpg, load_trpg, save_trpg

    card = load_trpg(player_id)
    if card is None:
        card = Trpg(player_id=str(player_id))
        save_trpg(card)
    return card


def roll_attributes(cfg: dict[str, Any] | None = None, rng: random.Random | None = None) -> dict[str, int]:
    """3d6 × 5 逐维掷骰（区间 15–90）。"""
    cfg = cfg if cfg is not None else _load_trpg_config()
    gen = cfg.get("attributes", {})
    count = int(gen.get("dice_count", 3))
    sides = int(gen.get("dice_sides", 6))
    mult = int(gen.get("multiplier", 5))
    dice = rng or random.Random()
    return {
        key: sum(dice.randint(1, sides) for _ in range(count)) * mult
        for key in gen.get("order", CHECK_ORDER)
    }


def initial_skills(attributes: dict[str, int], cfg: dict[str, Any] | None = None) -> dict[str, int]:
    """按 trpg.yaml 的 base / per 生成初始技能百分比（向下取整）。"""
    cfg = cfg if cfg is not None else _load_trpg_config()
    skills: dict[str, int] = {}
    for name, spec in (cfg.get("skills") or {}).items():
        value = int(spec.get("base", 0))
        for attr, scale in (spec.get("per") or {}).items():
            value += int(attributes.get(attr, 0) * float(scale))
        skills[name] = value
    return skills


def derived_values(attributes: dict[str, int], cfg: dict[str, Any] | None = None) -> dict[str, int]:
    """派生值（HP/MP/移动）：floor(base + Σ per×属性)。"""
    cfg = cfg if cfg is not None else _load_trpg_config()
    derived: dict[str, int] = {}
    for name, spec in (cfg.get("derived") or {}).items():
        value = float(spec.get("base", 0))
        for attr, scale in (spec.get("per") or {}).items():
            value += attributes.get(attr, 0) * float(scale)
        derived[name] = int(value)
    return derived


async def create_card(player_id: str | int, *, reroll: bool = False, card=None) -> dict[str, Any]:
    """掷六维并生成技能，落库。``reroll=True`` 时消耗一次重掷机会。"""
    cfg = _load_trpg_config()
    card = card if card is not None else await get_or_create_card(player_id)
    gen = cfg.get("attributes", {})
    max_rerolls = int(gen.get("rerolls", 1))
    used = int(card.stats.get("rerolls_used", 0))

    if card.attributes and not reroll:
        raise ValueError(_text("reroll_exists", "已有跑团卡，使用「/宠物.跑团 重掷」才能重来"))
    if reroll:
        if used >= max_rerolls:
            raise ValueError("重掷次数已用完")
        card.stats["rerolls_used"] = used + 1

    card.attributes = roll_attributes(cfg)
    card.skills = initial_skills(card.attributes, cfg)
    card.updated_at = datetime.now()
    from ..database import save_trpg

    save_trpg(card)
    return {
        "summary": card_summary(card, cfg),
        "rerolls_left": max_rerolls - int(card.stats.get("rerolls_used", 0)),
    }


def card_summary(card, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """/宠物.跑团 卡 的展示数据。"""
    cfg = cfg if cfg is not None else _load_trpg_config()
    names = cfg.get("attributes", {}).get("names", {})
    return {
        "attributes": [(key, names.get(key, key), card.attributes.get(key, 0)) for key in cfg.get("attributes", {}).get("order", CHECK_ORDER)],
        "skills": sorted(card.skills.items()),
        "derived": derived_values(card.attributes, cfg),
    }


# ---------------------------------------------------------------------------
# d100 检定（§13.2）
# ---------------------------------------------------------------------------

def check_target(skill_value: int, difficulty: str, cfg: dict[str, Any] | None = None) -> int:
    """难度目标：普通=技能值，困难=×0.5，极难=×0.2（向下取整）。"""
    cfg = cfg if cfg is not None else _load_trpg_config()
    check_cfg = cfg.get("check", {})
    scale = {"普通": 1.0, "困难": float(check_cfg.get("hard_scale", 0.5)), "极难": float(check_cfg.get("extreme_scale", 0.2))}.get(difficulty, 1.0)
    return int(skill_value * scale)


def check_tier(roll: int, skill_value: int, cfg: dict[str, Any] | None = None) -> str:
    """按 d100 结果返回档位（大成功/极难/困难/常规/失败/大失败）。"""
    cfg = cfg if cfg is not None else _load_trpg_config()
    c = cfg.get("check", {})
    critical = int(c.get("critical", 1))
    fumble_at = int(c.get("fumble_at", 100))
    fumble_below = int(c.get("fumble_skill_below", 50))

    if roll == critical:
        return "大成功"
    if roll >= fumble_at or (skill_value < fumble_below and roll >= 96):
        return "大失败"
    if roll <= int(skill_value * float(c.get("extreme_scale", 0.2))):
        return "极难成功"
    if roll <= int(skill_value * float(c.get("hard_scale", 0.5))):
        return "困难成功"
    if roll <= skill_value:
        return "常规成功"
    return "失败"


def resolve_check(skill_value: int, difficulty: str, rng: random.Random | None = None, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """掷一次检定，返回 档位、点数、目标与是否通过本难度。"""
    cfg = cfg if cfg is not None else _load_trpg_config()
    dice = rng or random.Random()
    roll = dice.randint(1, 100)
    target = check_target(skill_value, difficulty, cfg)
    tier = check_tier(roll, skill_value, cfg)
    passed = roll <= target or tier in ("大成功",)
    return {"roll": roll, "target": target, "tier": tier, "passed": passed, "difficulty": difficulty}


def format_check(skill_name: str, skill_value: int, result: dict[str, Any], *, passed: bool | None = None) -> str:
    """把一次检定渲染成消息行。``passed`` 与档位不一致时补充说明。"""
    ok = result["passed"] if passed is None else passed
    line = _text(
        "check_result",
        "🎲 {skill} 检定：掷出 {roll} / {target} —— {tier}（难度：{difficulty}）",
        skill=skill_name,
        roll=result["roll"],
        target=result["target"],
        tier=result["tier"],
        difficulty=result["difficulty"],
    )
    if passed is not None and passed != (result["tier"] in ("大成功", "极难成功", "困难成功", "常规成功")):
        line += _text("check_branch", "（未达到「{difficulty}」要求 → 失败分支）", difficulty=result["difficulty"]) if not passed else _text(
            "check_branch_ok", "（达到「{difficulty}」要求 → 成功分支）", difficulty=result["difficulty"]
        )
    return line


# ---------------------------------------------------------------------------
# 成长检定（§13.2）
# ---------------------------------------------------------------------------

def growth_check(
    card,
    used_skills: list[str],
    rng: random.Random | None = None,
    cfg: dict[str, Any] | None = None,
) -> list[tuple[str, int, int, int]]:
    """对本场用过的技能做成长检定。

    d100 > 当前技能值 → +1d10（不超过 cap）。返回 [(技能, 旧值, 新值, 掷骰)] 列表。
    """
    cfg = cfg if cfg is not None else _load_trpg_config()
    growth = cfg.get("growth", {})
    gain_die = int(growth.get("gain_die", 10))
    cap = int(growth.get("cap", 95))
    dice = rng or random.Random()
    results: list[tuple[str, int, int, int]] = []
    for skill_name in dict.fromkeys(used_skills):
        current = int(card.skills.get(skill_name, 0))
        if current <= 0:
            continue
        roll = dice.randint(1, 100)
        if roll > current:
            gain = dice.randint(1, gain_die)
            new_value = min(cap, current + gain)
            if new_value > current:
                card.skills[skill_name] = new_value
                results.append((skill_name, current, new_value, roll))
    if results:
        card.updated_at = datetime.now()
        from ..database import save_trpg

        save_trpg(card)
    return results


def format_growth(results: list[tuple[str, int, int, int]]) -> str:
    if not results:
        return _text("growth_none", "本场没有技能获得成长。")
    lines = [_text("growth_title", "📈 成长检定：")]
    for name, old, new, roll in results:
        lines.append(_text("growth_line", "· {skill}：{old} → {new}（掷出 {roll}）", skill=name, old=old, new=new, roll=roll))
    return "\n".join(lines)
