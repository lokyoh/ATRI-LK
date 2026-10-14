def _load_skills_config() -> dict:
    from ..config_loader import load_config

    return load_config("skills")


def check_skill_unlock(pet, skills_config: dict | None = None) -> list[str]:
    config = skills_config if skills_config is not None else _load_skills_config()
    learned = []
    for skill_id, skill in config.get("skills", {}).items():
        unlock_level = skill.get("unlock_condition", {}).get("level")
        if unlock_level is None or unlock_level > pet.level:
            continue
        if add_skill(pet, skill_id, skills_config=config):
            learned.append(skill_id)
    return learned


def add_skill(pet, skill_id: str, skills_config: dict | None = None) -> bool:
    config = skills_config if skills_config is not None else _load_skills_config()
    skill = config.get("skills", {}).get(skill_id)
    if not skill:
        return False

    slot_type = skill.get("type")
    if slot_type not in ("active", "passive"):
        return False

    pet_skills = pet.skills
    active_skills = pet_skills.setdefault("active", [])
    passive_skills = pet_skills.setdefault("passive", [])
    if skill_id in active_skills or skill_id in passive_skills:
        return False

    limits = config.get("limits", {})
    max_slots = limits.get(f"{slot_type}_slots")
    if max_slots is None or len(pet_skills[slot_type]) >= max_slots:
        return False

    pet_skills[slot_type].append(skill_id)
    pet.skill_proficiency[skill_id] = 0.0
    return True


def forget_skill(pet, skill_id: str) -> bool:
    for slot_type in ("active", "passive"):
        skills = pet.skills.setdefault(slot_type, [])
        if skill_id in skills:
            skills.remove(skill_id)
            pet.skill_proficiency.pop(skill_id, None)
            return True
    return False


def increase_proficiency(
    pet,
    skill_id: str,
    amount: float | None = None,
    skills_config: dict | None = None,
) -> None:
    config = skills_config if skills_config is not None else _load_skills_config()
    if skill_id not in pet.skills.get("active", []) + pet.skills.get("passive", []):
        return

    limits = config.get("limits", {})
    gain = limits.get("proficiency_gain", 0.01) if amount is None else amount
    maximum = limits.get("proficiency_max", 1.0)
    current = pet.skill_proficiency.get(skill_id, 0.0)
    pet.skill_proficiency[skill_id] = min(maximum, max(0.0, current + gain))


def get_skill_power(
    pet,
    skill_id: str,
    skills_config: dict | None = None,
) -> float:
    config = skills_config if skills_config is not None else _load_skills_config()
    skill = config.get("skills", {}).get(skill_id)
    if skill is None:
        raise ValueError(f"技能不存在：{skill_id}")

    limits = config.get("limits", {})
    maximum = limits.get("proficiency_max", 1.0)
    proficiency_bonus = limits.get("proficiency_power_bonus", 0.3)
    proficiency = min(maximum, max(0.0, pet.skill_proficiency.get(skill_id, 0.0)))
    return skill["power"] * (1.0 + proficiency * proficiency_bonus)