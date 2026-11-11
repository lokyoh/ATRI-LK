from .affection import AFFECTION_MAX, apply_affection_bonus
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
    """经验收益：状态 / 性格 / 年龄 / 轮回特权各是乘区，**好感最后叠、最多 +10%**。

    好感的加成不写进下面这串乘区，而是最后一步用
    :func:`core.affection.apply_affection_bonus` 加上去——那里按
    「加成后的总值」向下取整，与战斗输出共用同一套口径。
    """
    config = pet_config if pet_config is not None else _load_pet_config()
    mood = min(100, max(0, pet.mood))
    hunger = min(100, max(0, pet.hunger))
    fatigue = min(100, max(0, pet.fatigue))
    affection = min(AFFECTION_MAX, max(0, pet.affection))

    mood_mod = 0.5 + mood / 200
    hunger_mod = 0.5 + hunger / 200
    fatigue_mod = 1.0 - fatigue / 200
    personality_mod = get_personality_exp_mod(pet.personality, config)
    age_mod = max(0.5, 1.0 - max(0, pet.age_days) / 500)
    # 商店的两种经验加成口径不同、互不替代：
    #   exp_bonus  平值乘区（旧特权，0.1 = +10%），一次性生效
    #   exp_pct    百分比乘区（新商店的无限成长，0.01 = +1%/级）
    # 两者叠乘，因此老存档与新的无限成长可以共存。
    bonus = permanent_bonus or {}
    reincarnation_mod = (1.0 + float(bonus.get("exp_bonus", 0.0) or 0)) * (
        1.0 + float(bonus.get("exp_pct", 0.0) or 0)
    )

    total = (
        base_exp
        * mood_mod
        * hunger_mod
        * fatigue_mod
        * personality_mod
        * age_mod
        * reincarnation_mod
        * multiplier
    )
    return max(1, apply_affection_bonus(total, affection))


def get_personality_exp_mod(personality: str, pet_config: dict | None = None) -> float:
    config = pet_config if pet_config is not None else _load_pet_config()
    personality_config = config.get("personalities", {}).get(personality, {})
    return float(personality_config.get("exp_mod", 1.0))


def check_level_up(
    pet,
    *,
    max_level: int = 100,
    skill_granter=None,
    pet_config: dict | None = None,
    permanent_bonus: dict[str, int | float] | None = None,
    talent_config: dict | None = None,
) -> dict:
    """结算升级；达到等级里程碑时补发常规池技能（见 ``core.skills``）。

    ``skill_granter`` 是可选注入点：默认调 ``skills.grant_level_skills``，
    测试可传入确定性实现。
    """
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
    if skill_granter is None:
        from .skills import grant_level_skills

        skill_granter = grant_level_skills

    while pet.exp >= pet.exp_next and pet.level < max_level:
        pet.exp -= pet.exp_next
        pet.level += 1
        pet.talent_points += 1
        pet.exp_next = calc_exp_next(pet.level)
        result["level_up"] = True
        result["levels_gained"] += 1
        result["new_level"] = pet.level

    if result["level_up"]:
        # 里程碑按 pet.level 计算，跨多级时一次补齐（不需要在循环里逐级发）。
        for granted in skill_granter(pet):
            result["learned_skills"].append(granted)
        pet.attributes = calc_attributes_on_level_up(
            pet,
            config,
            permanent_bonus=permanent_bonus,
            talent_config=talent_config,
        )
    return result


def calc_exp_next(level: int) -> int:
    return int(100 * (max(1, level) ** 1.8))
