"""Reincarnation system: achievement points, perks and the cycle reset.

A cycle only ends when the player explicitly asks for it and confirms: the daily
settlement may *report* that reincarnation is available, but nothing here runs
automatically.  Biography writes are injected as an optional callback instead of
importing ``core.biography``, which is Step 11's deliverable.
"""

from datetime import datetime
from typing import Any

from ..utils.callbacks import Callback, run_callback
from .attributes import ATTRIBUTE_KEYS
from .leveling import calc_exp_next

_PER_ATTR_STEP = 100
"""Attribute points per ``per_100_attr`` weight."""

_PER_AFFECTION_STEP = 200
"""Affection scale is 0-1000, so each weight step is 200 points."""

_PER_DAYS_STEP = 10
"""Days per ``per_10_days`` weight."""


def _load_reincarnation_config() -> dict[str, Any]:
    from ..config_loader import load_config

    return load_config("reincarnation")


def _load_pet_config() -> dict[str, Any]:
    from ..config_loader import load_config

    return load_config("pet")


def _persist(reinc) -> None:
    from ..database import save_reincarnation

    save_reincarnation(reinc)


def calc_reincarnation_points(
    pet,
    reincarnation_config: dict[str, Any] | None = None,
) -> int:
    """Score a life: level, attributes, skills, affection and age."""
    cfg = (
        reincarnation_config
        if reincarnation_config is not None
        else _load_reincarnation_config()
    )
    weights = cfg["point_weights"]

    points = pet.level * weights["per_level"]

    attr_sum = sum(pet.attributes.get(key, 0) for key in ATTRIBUTE_KEYS)
    points += (attr_sum // _PER_ATTR_STEP) * weights["per_100_attr"]

    skill_count = len(pet.skills["active"]) + len(pet.skills["passive"])
    points += skill_count * weights["per_skill"]

    points += (pet.affection // _PER_AFFECTION_STEP) * weights["per_200_affection"]
    points += (pet.age_days // _PER_DAYS_STEP) * weights["per_10_days"]

    if pet.age_days >= pet.lifespan_days:
        points += weights["full_lifespan_bonus"]

    return max(1, points)


async def check_reincarnation_available(
    pet,
    reincarnation_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Report whether the pet *may* reincarnate; never performs it."""
    cfg = (
        reincarnation_config
        if reincarnation_config is not None
        else _load_reincarnation_config()
    )
    reasons = []
    if pet.age_days >= pet.lifespan_days:
        reasons.append("寿命已尽")
    if pet.level >= cfg["max_level"]:
        reasons.append("已达等级上限")
    if pet.level >= cfg["min_level_for_manual"]:
        reasons.append("可主动轮回")

    return {
        "can_reincarnate": bool(reasons),
        "reasons": reasons,
        "estimated_points": calc_reincarnation_points(pet, cfg),
    }


async def perform_reincarnation(
    pet,
    player_id: str | int,
    manual: bool = False,
    *,
    reincarnation_config: dict[str, Any] | None = None,
    pet_config: dict[str, Any] | None = None,
    biography_hook: Callback | None = None,
) -> dict[str, Any]:
    """End the current life and start the next one.

    Only call this after the player has explicitly confirmed; the daily
    settlement must never reach it.
    """
    cfg = (
        reincarnation_config
        if reincarnation_config is not None
        else _load_reincarnation_config()
    )
    if manual and pet.level < cfg["min_level_for_manual"]:
        # 等级门槛只拦「提前主动轮回」。寿命已尽或已满级属于自然终结，
        # 必须放行，否则低等级宠物在寿命结束后会永远无法轮回。
        natural_ending = (
            pet.age_days >= pet.lifespan_days or pet.level >= cfg["max_level"]
        )
        if not natural_ending:
            raise ValueError(f"主动轮回需要至少 {cfg['min_level_for_manual']} 级")

    points = calc_reincarnation_points(pet, cfg)
    ending = generate_ending(pet)

    await run_callback(
        biography_hook,
        pet,
        {
            "type": "ending",
            "title": f"第 {pet.cycle_count + 1} 世终结",
            "content": ending,
        },
    )

    reinc = await get_or_create_reincarnation(player_id)

    reinc.past_pets.append(
        {
            "name": pet.name,
            "level": pet.level,
            "age_days": pet.age_days,
            "cycle": pet.cycle_count + 1,
            "ending": ending,
            "points_earned": points,
            "finalized_at": datetime.now().isoformat(),
        }
    )
    reinc.achievement_points += points
    reinc.total_cycles += 1
    if pet.level > reinc.best_level:
        reinc.best_level = pet.level
        reinc.best_attributes = dict(pet.attributes)
    if pet.age_days > reinc.best_age_days:
        reinc.best_age_days = pet.age_days

    reset_pet_for_reincarnation(pet, reinc, pet_config=pet_config)
    pet.cycle_count = reinc.total_cycles
    reinc.updated_at = datetime.now()
    _persist(reinc)

    return {
        "points_earned": points,
        "total_points": reinc.achievement_points,
        "cycle": reinc.total_cycles,
        "ending": ending,
    }


def reset_pet_for_reincarnation(
    pet,
    reinc,
    *,
    pet_config: dict[str, Any] | None = None,
) -> None:
    """Put the pet back to the start of a new life, keeping the achievements."""
    from .attributes import calc_attributes_on_level_up

    pet.level = 1 + reinc.permanent_bonuses.get("start_level_bonus", 0)
    pet.exp = 0
    # 起始等级可能被特权抬高，exp_next 必须跟着等级走，不能固定写 100。
    pet.exp_next = calc_exp_next(pet.level)
    pet.age_days = 0
    pet.current_day = 0
    pet.last_settlement_day = 0
    pet.cycle_started_at = datetime.now()
    pet.mood = 80
    pet.hunger = 80
    pet.fatigue = 0
    pet.cleanliness = 100
    pet.affection = 0
    pet.health_state = "healthy"
    pet.emotion_state = "normal"
    pet.skills = {"active": [], "passive": []}
    pet.skill_proficiency = {}
    pet.talent_points = 0
    pet.talents = {}
    pet.trained_attributes = {}
    pet.action_points = 10
    pet.daily_flags = {}
    pet.is_alive = True

    cfg = pet_config if pet_config is not None else _load_pet_config()
    pet.attributes = calc_attributes_on_level_up(pet, cfg)


def generate_ending(pet) -> str:
    """Describe how the finished life ended."""
    if pet.age_days >= pet.lifespan_days:
        return f"{pet.name}安详地闭上了眼睛，它的一生充满了冒险与陪伴。"
    elif pet.level >= 100:
        return f"{pet.name}达到了巅峰，成为传说中的存在。"
    else:
        return f"{pet.name}选择了主动轮回，期待下一次相遇。"


async def get_or_create_reincarnation(player_id: str | int):
    """Return the player's reincarnation row, creating it with defaults if new."""
    from ..database import Reincarnation, load_reincarnation, save_reincarnation

    reinc = load_reincarnation(str(player_id))
    if reinc is None:
        reinc = Reincarnation(player_id=str(player_id))
        save_reincarnation(reinc)
    return reinc


async def get_permanent_bonus(player_id: str | int) -> dict[str, Any]:
    """Return the permanent bonuses unlocked by spent achievement points."""
    reinc = await get_or_create_reincarnation(player_id)
    return reinc.permanent_bonuses


async def spend_achievement_points(
    player_id: str | int,
    perk_id: str,
    *,
    reincarnation_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Exchange achievement points for a permanent perk."""
    cfg = (
        reincarnation_config
        if reincarnation_config is not None
        else _load_reincarnation_config()
    )
    perk = cfg["perks"].get(perk_id)
    if not perk:
        raise ValueError("特权不存在")

    reinc = await get_or_create_reincarnation(player_id)
    available = reinc.achievement_points - reinc.spent_points
    if available < perk["cost"]:
        raise ValueError(f"成就点不足，需要 {perk['cost']}，可用 {available}")

    reinc.spent_points += perk["cost"]
    reinc.unlocked_perks.append(perk_id)
    for key, value in perk["effect"].items():
        reinc.permanent_bonuses[key] = reinc.permanent_bonuses.get(key, 0) + value
    reinc.updated_at = datetime.now()
    _persist(reinc)

    return {
        "perk": perk_id,
        "remaining_points": reinc.achievement_points - reinc.spent_points,
    }
