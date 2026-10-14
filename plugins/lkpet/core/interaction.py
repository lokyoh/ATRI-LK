"""Interaction system: the actions that spend the pet's daily action points.

Every function validates its action-point cost against ``daily.yaml`` and
applies its effect from ``interactions.yaml``.

These actions deliberately write **nothing** to the biography: feeding, playing
and bathing are routine, and ``core.biography`` only keeps biography-worthy
events (``milestone`` / ``key_choice`` / ``battle_victory`` / ``first_time`` /
``ending``).  The life story is assembled by ``core.daily`` and
``core.reincarnation`` instead.
"""

from typing import Any

from .affection import add_affection
from .leveling import check_level_up
from .status import clamp_status, update_emotion_state, update_health_state

TRAINABLE_ATTRIBUTES = ("hp", "atk", "def", "spd", "mp")
"""Attributes ``/宠物.训练`` is allowed to raise."""

FATIGUE_TRAIN_LIMIT = 80
"""Training is refused while fatigue is above this value."""

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


async def feed(
    pet,
    food_id: str,
    *,
    interactions_config: dict[str, Any] | None = None,
    daily_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Feed the pet a food from ``interactions.yaml``, spending action points."""
    cfg = (
        interactions_config
        if interactions_config is not None
        else _load_interactions_config()
    )
    food = cfg["foods"].get(food_id)
    if not food:
        raise ValueError("这不是食物")

    check_action_point(pet, "feed", daily_config=daily_config)
    effects = food["effects"]
    pet.hunger = clamp_status(pet.hunger + effects.get("hunger", 0), _STATUS_FLOOR)
    pet.mood = clamp_status(pet.mood + effects.get("mood", 0), _STATUS_FLOOR)
    add_affection(pet, effects.get("affection", 1))
    _mark_interaction(pet, "feed_count")
    pet.daily_flags["fed"] = True
    consume_action_point(pet, "feed", daily_config=daily_config)
    update_emotion_state(pet)
    return {
        "hunger": pet.hunger,
        "mood": pet.mood,
        "affection": pet.affection,
        "action_points": pet.action_points,
        "message": food.get("message", f"吃掉了{food['name']}"),
    }


async def play(
    pet,
    *,
    interactions_config: dict[str, Any] | None = None,
    daily_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Play with the pet; raises mood and affection at the cost of fatigue."""
    cfg = (
        interactions_config
        if interactions_config is not None
        else _load_interactions_config()
    )
    effects = cfg["effects"]["play"]
    check_action_point(pet, "play", daily_config=daily_config)
    pet.mood = clamp_status(pet.mood + effects["mood"], _STATUS_FLOOR)
    pet.fatigue = clamp_status(pet.fatigue + effects["fatigue"], _STATUS_FLOOR)
    add_affection(pet, effects["affection"])
    _mark_interaction(pet, "play_count")
    pet.daily_flags["played"] = True
    consume_action_point(pet, "play", daily_config=daily_config)
    update_emotion_state(pet)
    update_health_state(pet)
    return {
        "mood": pet.mood,
        "fatigue": pet.fatigue,
        "action_points": pet.action_points,
        "message": get_play_message(pet),
    }


async def pet_pet(
    pet,
    *,
    interactions_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pet the pet; free of action-point cost, raises affection and mood."""
    cfg = (
        interactions_config
        if interactions_config is not None
        else _load_interactions_config()
    )
    effects = cfg["effects"]["pet"]
    add_affection(pet, effects["affection"])
    pet.mood = clamp_status(pet.mood + effects["mood"], _STATUS_FLOOR)
    _mark_interaction(pet, "pet_count")
    pet.daily_flags["petted"] = True
    update_emotion_state(pet)
    return {
        "affection": pet.affection,
        "action_points": pet.action_points,
        "message": get_pet_message(pet),
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
    _mark_interaction(pet, "bathe_count")
    pet.daily_flags["bathed"] = True
    consume_action_point(pet, "bathe", daily_config=daily_config)
    update_health_state(pet)
    return {
        "cleanliness": pet.cleanliness,
        "action_points": pet.action_points,
        "message": get_bathe_message(pet),
    }


async def train(
    pet,
    attribute: str,
    *,
    interactions_config: dict[str, Any] | None = None,
    daily_config: dict[str, Any] | None = None,
    pet_config: dict[str, Any] | None = None,
    permanent_bonus: dict[str, int | float] | None = None,
    talent_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Train one attribute; grants experience and may trigger a level-up."""
    cfg = (
        interactions_config
        if interactions_config is not None
        else _load_interactions_config()
    )
    if attribute not in TRAINABLE_ATTRIBUTES:
        raise ValueError("无效的属性")

    check_action_point(pet, "train", daily_config=daily_config)
    if pet.fatigue > FATIGUE_TRAIN_LIMIT:
        raise ValueError("宠物太累了，先休息吧")

    effects = cfg["effects"]["train"]
    gain = effects["gain"]
    pet.attributes[attribute] += gain
    # 单独累计训练增益，否则下次升级重算属性时会把它抹掉。
    pet.trained_attributes[attribute] = (
        pet.trained_attributes.get(attribute, 0) + gain
    )
    pet.fatigue = clamp_status(pet.fatigue + effects["fatigue"], _STATUS_FLOOR)
    pet.exp += effects["exp"]
    _mark_interaction(pet, "train_count")
    pet.daily_flags["trained"] = True
    consume_action_point(pet, "train", daily_config=daily_config)

    level_result = check_level_up(
        pet,
        pet_config=pet_config,
        permanent_bonus=permanent_bonus,
        talent_config=talent_config,
    )
    return {
        "attribute": attribute,
        "gain": gain,
        "fatigue": pet.fatigue,
        "action_points": pet.action_points,
        "level_up": level_result,
    }


async def rest(
    pet,
    *,
    interactions_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Let the pet sleep; free of action-point cost, recovers fatigue."""
    cfg = (
        interactions_config
        if interactions_config is not None
        else _load_interactions_config()
    )
    effects = cfg["effects"]["rest"]
    pet.fatigue = max(0, pet.fatigue - effects["fatigue_recover"])
    pet.mood = clamp_status(pet.mood + effects["mood"], _STATUS_FLOOR)
    _mark_interaction(pet, "rest_count")
    pet.daily_flags["rested"] = True
    update_health_state(pet)

    from ..utils.texts import text

    return {
        "fatigue": pet.fatigue,
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


def get_pet_message(pet) -> str:
    from ..utils.texts import text

    if pet.affection >= 800:
        return text("messages.pet.high", f"{pet.name}蹭了蹭你的手，眼神温柔", name=pet.name)
    elif pet.affection >= 500:
        return text("messages.pet.mid", f"{pet.name}安静地享受你的抚摸", name=pet.name)
    else:
        return text("messages.pet.low", f"{pet.name}有点警惕地看着你", name=pet.name)


def get_bathe_message(pet) -> str:
    from ..utils.texts import text

    if pet.mood >= 60:
        return text("messages.bathe.willing", f"{pet.name}乖乖地让你洗完了澡", name=pet.name)
    else:
        return text("messages.bathe.unwilling", f"{pet.name}不太情愿地洗完了澡", name=pet.name)
