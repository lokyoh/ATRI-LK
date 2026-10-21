ATTRIBUTE_KEYS = ("hp", "atk", "def", "spd", "mp")


def _load_pet_config() -> dict:
    from ..config_loader import load_config

    return load_config("pet")


def _load_talent_config() -> dict:
    from ..config_loader import load_config

    return load_config("talents")


def calc_attributes_on_level_up(
    pet,
    base_config: dict | None = None,
    permanent_bonus: dict[str, int | float] | None = None,
    talent_config: dict | None = None,
) -> dict[str, int]:
    """Recalculate level-based attributes using the shared, race-free model."""
    cfg = base_config if base_config is not None else _load_pet_config()
    base = cfg["base_attributes"]
    growth = cfg["growth"]
    qualification = pet.qualification
    bonuses = permanent_bonus or {}
    level = max(1, pet.level)

    attributes = {
        key: int(base[key] + growth[key] * level * qualification.get(key, 1.0))
        for key in ATTRIBUTE_KEYS
    }
    attributes["luk"] = int(base.get("luk", 5) + level // 5)
    # 好感放大 10 倍后为 0-1000，除以 100 仍映射为 0-10 点 cha 加成。
    attributes["cha"] = int(base.get("cha", 5) + pet.affection // 100)
    attributes["int"] = int(base.get("int", 5) + level // 4)

    for key in ATTRIBUTE_KEYS:
        attributes[key] += int(bonuses.get(f"{key}_bonus", 0))

    age_multiplier = 1.0 + get_age_attribute_bonus(pet.age_days)
    for key in ("hp", "atk", "def"):
        attributes[key] = int(attributes[key] * age_multiplier)

    # 训练指令已移除（2026-10-16）：trained_attributes 旧列保留在库里但不再读取，
    # 与 PETDATA.LOVE 同策略——历史训练增益自本次改版起不再生效。

    if pet.talents:
        talents_cfg = (
            talent_config if talent_config is not None else _load_talent_config()
        )
        for talent_id, talent_level in pet.talents.items():
            talent = talents_cfg.get("talents", {}).get(talent_id)
            if talent is None:
                continue
            for key, value in talent.get("effect_per_level", {}).items():
                if key in attributes and isinstance(value, int | float):
                    attributes[key] += int(value * talent_level)

    return attributes


def get_age_attribute_bonus(age_days: int) -> float:
    return min(0.3, max(0, age_days) / 1000)


def apply_equipment_bonus(
    attributes: dict[str, int | float],
    equipment: dict | None,
) -> dict[str, int | float]:
    final = dict(attributes)
    for item in (equipment or {}).values():
        if not isinstance(item, dict):
            continue
        bonuses = item.get("bonus", {})
        if not isinstance(bonuses, dict):
            continue
        for key, value in bonuses.items():
            if isinstance(value, int | float):
                final[key] = final.get(key, 0) + value
    return final


def apply_title_bonus(
    attributes: dict[str, int | float],
    title_config: dict | None,
) -> dict[str, int | float]:
    final = dict(attributes)
    if not title_config:
        return final

    bonuses = title_config.get("bonus", {})
    if not isinstance(bonuses, dict):
        return final
    for key, value in bonuses.items():
        if isinstance(value, int | float):
            final[key] = final.get(key, 0) + value
    return final


def get_final_attributes(
    pet,
    base_config: dict | None = None,
) -> dict[str, int | float]:
    cfg = base_config if base_config is not None else _load_pet_config()
    attributes = apply_equipment_bonus(pet.attributes, pet.equipment)
    title_config = cfg.get("titles", {}).get(pet.title)
    return apply_title_bonus(attributes, title_config)
