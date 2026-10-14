from .affection import AFFECTION_MAX
from .attributes import calc_attributes_on_level_up


def _load_pet_config() -> dict:
    from ..config_loader import load_config

    return load_config("pet")


def calc_exp_gain(
    base_exp: int,
    pet,
    multiplier: float = 1.0,
    *,
    pet_config: dict | None = None,
    permanent_bonus: dict[str, int | float] | None = None,
) -> int:
    config = pet_config if pet_config is not None else _load_pet_config()
    mood = min(100, max(0, pet.mood))
    hunger = min(100, max(0, pet.hunger))
    fatigue = min(100, max(0, pet.fatigue))
    affection = min(AFFECTION_MAX, max(0, pet.affection))

    mood_mod = 0.5 + mood / 200
    hunger_mod = 0.5 + hunger / 200
    fatigue_mod = 1.0 - fatigue / 200
    affection_mod = 1.0 + (affection / AFFECTION_MAX) * 0.3
    personality_mod = get_personality_exp_mod(pet.personality, config)
    age_mod = max(0.5, 1.0 - max(0, pet.age_days) / 500)
    reincarnation_mod = 1.0 + (permanent_bonus or {}).get("exp_bonus", 0.0)

    total = (
        base_exp
        * mood_mod
        * hunger_mod
        * fatigue_mod
        * affection_mod
        * personality_mod
        * age_mod
        * reincarnation_mod
        * multiplier
    )
    return max(1, int(total))


def get_personality_exp_mod(personality: str, pet_config: dict | None = None) -> float:
    config = pet_config if pet_config is not None else _load_pet_config()
    personality_config = config.get("personalities", {}).get(personality, {})
    return float(personality_config.get("exp_mod", 1.0))


def check_level_up(
    pet,
    *,
    max_level: int = 100,
    skill_unlocker=None,
    pet_config: dict | None = None,
    permanent_bonus: dict[str, int | float] | None = None,
    talent_config: dict | None = None,
) -> dict:
    result = {
        "level_up": False,
        "old_level": pet.level,
        "new_level": pet.level,
        "levels_gained": 0,
        "learned_skills": [],
    }
    if pet.level >= max_level:
        return result

    config = pet_config if pet_config is not None else _load_pet_config()
    if skill_unlocker is None:
        from .skills import check_skill_unlock

        skill_unlocker = check_skill_unlock

    while pet.exp >= pet.exp_next and pet.level < max_level:
        pet.exp -= pet.exp_next
        pet.level += 1
        pet.talent_points += 1
        pet.exp_next = calc_exp_next(pet.level)
        result["level_up"] = True
        result["levels_gained"] += 1
        result["new_level"] = pet.level
        result["learned_skills"].extend(skill_unlocker(pet))

    if result["level_up"]:
        pet.attributes = calc_attributes_on_level_up(
            pet,
            config,
            permanent_bonus=permanent_bonus,
            talent_config=talent_config,
        )
    return result


def calc_exp_next(level: int) -> int:
    return int(100 * (max(1, level) ** 1.8))