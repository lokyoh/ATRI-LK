"""Interaction system: the actions that spend the pet's daily action points.

Every function validates its action-point cost against ``daily.yaml`` and
applies its effect from ``interactions.yaml``.  喂食/玩耍/洗澡/休息 的亲密度
收益统一为 **3d10 随机骰**（``interactions.yaml`` 的 ``affection_roll``）。

These actions deliberately write **nothing** to the biography: feeding, playing
and bathing are routine, and ``core.biography`` only keeps biography-worthy
events (``milestone`` / ``key_choice`` / ``battle_victory`` / ``first_time`` /
``ending``).  The life story is assembled by ``core.daily`` and
``core.reincarnation`` instead.
"""

import random
from typing import Any

from .affection import add_affection
from .status import clamp_status, update_emotion_state, update_health_state

_STATUS_FLOOR = 0
"""Interaction effects clamp statuses into ``[0, 100]``."""


def _load_daily_config() -> dict[str, Any]:
    from ..config_loader import load_config

    return load_config("daily")


def _load_interactions_config() -> dict[str, Any]:
    from ..config_loader import load_config

    return load_config("interactions")


def _action_costs(daily_config: dict[str, Any] | None) -> dict[str, int]:
    cfg = daily_config if daily_config is not None else _load_daily_config()
    return cfg["action_points"]["costs"]


def _mark_interaction(pet, counter: str) -> None:
    """Count one interaction and stamp the personal day it happened on.

    ``stats["last_interaction_day"]`` is kept as a record only; the affection
    decay rule lives in ``core.status`` and keys off unmet care needs.
    """
    pet.stats[counter] = pet.stats.get(counter, 0) + 1
    pet.stats["last_interaction_day"] = pet.age_days


def check_action_point(
    pet,
    action: str,
    *,
    daily_config: dict[str, Any] | None = None,
) -> None:
    """Raise ``ValueError`` when the pet cannot afford ``action``."""
    cost = _action_costs(daily_config).get(action, 0)
    if pet.action_points < cost:
        raise ValueError(f"行动点不足，需要 {cost} 点，当前 {pet.action_points} 点")


def consume_action_point(
    pet,
    action: str,
    *,
    daily_config: dict[str, Any] | None = None,
) -> None:
    """Deduct the action-point cost of ``action``, never going below zero."""
    cost = _action_costs(daily_config).get(action, 0)
    pet.action_points = max(0, pet.action_points - cost)


def _affection_roll(interactions_config: dict[str, Any] | None = None) -> int:
    """互动好感收益：3d10 随机骰（骰子规格外置 interactions.yaml 的 affection_roll）。"""
    cfg = (
        interactions_config
        if interactions_config is not None
        else _load_interactions_config()
    )
    roll = cfg.get("affection_roll", {"count": 3, "sides": 10})
    return sum(random.randint(1, int(roll["sides"])) for _ in range(int(roll["count"])))


def _feed_message(pet) -> str:
    from ..utils.texts import text

    return text("messages.feed", f"{pet.name}开心地吃下了食物", name=pet.name)


async def feed(
    pet,
    *,
    interactions_config: dict[str, Any] | None = None,
    daily_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Feed the pet; restores a fixed hunger amount from ``interactions.yaml``."""
    cfg = (
        interactions_config
        if interactions_config is not None
        else _load_interactions_config()
    )
    effects = cfg["effects"]["feed"]
    gain = int(effects["hunger"])

    check_action_point(pet, "feed", daily_config=daily_config)
    pet.hunger = clamp_status(pet.hunger + gain, _STATUS_FLOOR)
    pet.mood = clamp_status(pet.mood + effects.get("mood", 0), _STATUS_FLOOR)
    roll = _affection_roll(cfg)
    add_affection(pet, roll)
    _mark_interaction(pet, "feed_count")
    pet.daily_flags["fed"] = True
    consume_action_point(pet, "feed", daily_config=daily_config)
    update_emotion_state(pet)
    return {
        "hunger": pet.hunger,
        "mood": pet.mood,
        "affection": pet.affection,
        "affection_gain": roll,
        "action_points": pet.action_points,
        "message": _feed_message(pet),
    }


async def play(
    pet,
    *,
    interactions_config: dict[str, Any] | None = None,
    daily_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Play with the pet; raises mood at the cost of fatigue; 3d10 affection."""
    cfg = (
        interactions_config
        if interactions_config is not None
        else _load_interactions_config()
    )
    effects = cfg["effects"]["play"]
    check_action_point(pet, "play", daily_config=daily_config)
    pet.mood = clamp_status(pet.mood + effects["mood"], _STATUS_FLOOR)
    pet.fatigue = clamp_status(pet.fatigue + effects["fatigue"], _STATUS_FLOOR)
    roll = _affection_roll(cfg)
    add_affection(pet, roll)
    _mark_interaction(pet, "play_count")
    pet.daily_flags["played"] = True
    consume_action_point(pet, "play", daily_config=daily_config)
    update_emotion_state(pet)
    update_health_state(pet)
    return {
        "mood": pet.mood,
        "fatigue": pet.fatigue,
        "affection": pet.affection,
        "affection_gain": roll,
        "action_points": pet.action_points,
        "message": get_play_message(pet),
    }


async def bathe(
    pet,
    *,
    interactions_config: dict[str, Any] | None = None,
    daily_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bathe the pet; raises cleanliness but costs a little mood."""
    cfg = (
        interactions_config
        if interactions_config is not None
        else _load_interactions_config()
    )
    effects = cfg["effects"]["bathe"]
    check_action_point(pet, "bathe", daily_config=daily_config)
    pet.cleanliness = clamp_status(
        pet.cleanliness + effects["cleanliness"], _STATUS_FLOOR
    )
    pet.mood = clamp_status(pet.mood + effects["mood"], _STATUS_FLOOR)
    roll = _affection_roll(cfg)
    add_affection(pet, roll)
    _mark_interaction(pet, "bathe_count")
    pet.daily_flags["bathed"] = True
    consume_action_point(pet, "bathe", daily_config=daily_config)
    update_health_state(pet)
    return {
        "cleanliness": pet.cleanliness,
        "affection": pet.affection,
        "affection_gain": roll,
        "action_points": pet.action_points,
        "message": get_bathe_message(pet),
    }


async def rest(
    pet,
    *,
    interactions_config: dict[str, Any] | None = None,
    daily_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Let the pet sleep; spends action points, recovers fatigue; 3d10 affection."""
    cfg = (
        interactions_config
        if interactions_config is not None
        else _load_interactions_config()
    )
    effects = cfg["effects"]["rest"]
    check_action_point(pet, "rest", daily_config=daily_config)
    roll = _affection_roll(cfg)
    add_affection(pet, roll)
    pet.fatigue = max(0, pet.fatigue - effects["fatigue_recover"])
    pet.mood = clamp_status(pet.mood + effects["mood"], _STATUS_FLOOR)
    _mark_interaction(pet, "rest_count")
    pet.daily_flags["rested"] = True
    consume_action_point(pet, "rest", daily_config=daily_config)
    update_health_state(pet)

    from ..utils.texts import text

    return {
        "fatigue": pet.fatigue,
        "affection": pet.affection,
        "affection_gain": roll,
        # rest.ok 模板含 {action_points}：生产者必须带上扣点后的当前值。
        "action_points": pet.action_points,
        "message": text("messages.rest", f"{pet.name}美美地睡了一觉", name=pet.name),
    }


def get_play_message(pet) -> str:
    from ..utils.texts import text

    if pet.mood >= 80:
        return text("messages.play.happy", f"{pet.name}开心地蹦蹦跳跳", name=pet.name)
    elif pet.mood >= 50:
        return text("messages.play.normal", f"{pet.name}和你玩了一会儿", name=pet.name)
    else:
        return text("messages.play.low", f"{pet.name}有点心不在焉", name=pet.name)


def get_bathe_message(pet) -> str:
    from ..utils.texts import text

    if pet.mood >= 60:
        return text(
            "messages.bathe.willing", f"{pet.name}乖乖地让你洗完了澡", name=pet.name
        )
    else:
        return text(
            "messages.bathe.unwilling", f"{pet.name}不太情愿地洗完了澡", name=pet.name
        )


async def train(
    pet,
    player_id: str | int,
    *,
    interactions_config: dict[str, Any] | None = None,
    daily_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """训练宠物获取经验；消耗行动点和状态，经验根据肉鸽历史最高层数动态调整。"""
    from ..database import load_rogue
    from .leveling import calc_exp_gain, check_level_up

    cfg = (
        interactions_config
        if interactions_config is not None
        else _load_interactions_config()
    )
    effects = cfg["effects"]["train"]
    check_action_point(pet, "train", daily_config=daily_config)

    # 读取肉鸽历史最高层数
    rogue = load_rogue(player_id)
    best_floor = rogue.best_floor if rogue else 0

    # 根据最高层数匹配经验档位
    exp_tiers = effects.get("exp_tiers", [])
    base_exp = effects.get("exp_base", 500)
    for tier in sorted(exp_tiers, key=lambda t: t.get("floor", 0), reverse=True):
        if best_floor >= tier.get("floor", 0):
            base_exp = tier.get("exp", base_exp)
            break

    # 消耗状态
    pet.hunger = clamp_status(pet.hunger + effects.get("hunger", 0), _STATUS_FLOOR)
    pet.fatigue = clamp_status(pet.fatigue + effects.get("fatigue", 0), _STATUS_FLOOR)
    pet.mood = clamp_status(pet.mood + effects.get("mood", 0), _STATUS_FLOOR)

    # 计算经验（带状态修正）
    exp_gain = calc_exp_gain(base_exp, pet)
    pet.exp += exp_gain

    # 检查升级
    level_result = check_level_up(pet)

    _mark_interaction(pet, "train_count")
    consume_action_point(pet, "train", daily_config=daily_config)
    update_emotion_state(pet)
    update_health_state(pet)

    return {
        "name": pet.name,
        "exp": exp_gain,
        "level": pet.level,
        "hunger": pet.hunger,
        "fatigue": pet.fatigue,
        "mood": pet.mood,
        "action_points": pet.action_points,
        "level_up": level_result.get("level_up", False),
        "old_level": level_result.get("old_level", pet.level),
        "new_level": level_result.get("new_level", pet.level),
        "best_floor": best_floor,
        "learned_skills": level_result.get("learned_skills", []),
        "message": _train_message(pet, level_result),
    }


def _train_message(pet, level_result: dict) -> str:
    from ..utils.texts import text

    if level_result.get("level_up"):
        lines = [
            text(
                "messages.train.level_up",
                f"{pet.name}升级了！Lv.{level_result['old_level']} → Lv.{level_result['new_level']}",
                name=pet.name,
                old_level=level_result["old_level"],
                new_level=level_result["new_level"],
            )
        ]
    else:
        lines = [
            text("messages.train.normal", f"{pet.name}完成了训练", name=pet.name)
        ]

    # 升级里程碑发的技能是静默入池/自动装备的，这里必须告诉玩家。
    learned = level_result.get("learned_skills") or []
    if learned:
        names = "、".join(
            str(item.get("name") or item.get("id", "")) for item in learned
        )
        lines.append(text("skill.granted", "📖 学会新技能：{names}", names=names))
        benched = [
            str(item.get("name") or item.get("id", ""))
            for item in learned
            if not item.get("equipped")
        ]
        if benched:
            lines.append(
                text(
                    "skill.granted_full",
                    "📖 新技能 {name} 已进入技能库（装备槽已满，可在 /宠物.技能 中调整）",
                    name="、".join(benched),
                )
            )
    return "\n".join(lines)

