def _load_talent_config() -> dict:
    from ..config_loader import load_config

    return load_config("talents")


def describe_talents(talent_config: dict | None = None) -> list[str]:
    """Selectable talents as Chinese names, for usage messages."""
    config = talent_config if talent_config is not None else _load_talent_config()
    return [
        str(item.get("name") or key)
        for key, item in config.get("talents", {}).items()
    ]


def resolve_talent(value: str, talent_config: dict | None = None) -> str:
    """Map a player-supplied talent to its config key.

    Accepts both the Chinese name (``力量强化``) and the English key
    (``atk_1``), mirroring ``pet_core.resolve_personality``: usage texts
    advertise Chinese, storage keeps keys.
    """
    config = talent_config if talent_config is not None else _load_talent_config()
    talents = config.get("talents", {})
    wanted = (value or "").strip()
    options = " / ".join(describe_talents(config))
    if not wanted:
        raise ValueError(f"请选择天赋：{options}")
    lowered = wanted.lower()
    for key, item in talents.items():
        candidates = {str(key).lower(), str(item.get("name") or "").strip().lower()}
        if lowered in candidates:
            return key
    raise ValueError(f"无效的天赋：{wanted}（可选：{options}）")


def allocate_talent(pet, talent_id: str, talent_config: dict | None = None) -> dict:
    if pet.talent_points <= 0:
        raise ValueError("没有可用的天赋点")

    config = talent_config if talent_config is not None else _load_talent_config()
    talent = config.get("talents", {}).get(talent_id)
    if talent is None:
        raise ValueError("天赋不存在")

    current_level = pet.talents.get(talent_id, 0)
    max_level = talent["max_level"]
    if current_level >= max_level:
        raise ValueError("该天赋已满级")

    effect = talent.get("effect_per_level", {})
    if not effect or any(
        key not in pet.attributes or not isinstance(value, (int, float))
        for key, value in effect.items()
    ):
        raise ValueError("天赋效果配置无效")

    new_level = current_level + 1
    apply_talent_effect(pet, talent)
    pet.talents[talent_id] = new_level
    pet.talent_points -= 1
    return {
        "talent_id": talent_id,
        "name": talent.get("name", talent_id),
        "new_level": new_level,
        "remaining_points": pet.talent_points,
    }


def apply_talent_effect(pet, talent: dict, levels: int = 1) -> None:
    """Apply the per-rank delta; `levels` is the number of ranks being added."""
    if levels <= 0:
        return
    for key, value in talent.get("effect_per_level", {}).items():
        if key in pet.attributes:
            pet.attributes[key] += int(value * levels)


def reset_talents(pet, talent_config: dict | None = None) -> dict:
    config = talent_config if talent_config is not None else _load_talent_config()
    total_points = sum(pet.talents.values())
    removed_bonuses = {}

    for talent_id, talent_level in pet.talents.items():
        talent = config.get("talents", {}).get(talent_id)
        if talent is None:
            raise ValueError(f"天赋配置不存在，无法重置：{talent_id}")
        for key, value in talent.get("effect_per_level", {}).items():
            if key not in pet.attributes or not isinstance(value, (int, float)):
                raise ValueError(f"天赋效果配置无效：{talent_id}")
            removed_bonuses[key] = removed_bonuses.get(key, 0) + int(value * talent_level)

    for key, value in removed_bonuses.items():
        pet.attributes[key] -= value
    pet.talents = {}
    pet.talent_points += total_points
    return {"refunded": total_points, "removed_bonuses": removed_bonuses}