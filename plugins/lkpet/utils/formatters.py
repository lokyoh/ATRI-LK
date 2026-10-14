"""Display layer: turn plugin state into the messages players read.

Every template comes from ``texts.yaml`` (via :func:`utils.texts.text`); this
module only assembles the fields.  Enum keys that have no YAML counterpart
(``rarity`` / ``health_state`` / ``emotion_state``) get a small label map here.
"""

from typing import Any

from .texts import text

RARITY_LABELS = {
    "common": "普通",
    "rare": "稀有",
    "epic": "史诗",
    "legendary": "传说",
    "mythic": "神话",
}

HEALTH_LABELS = {
    "healthy": "健康",
    "tired": "疲惫",
    "sick": "生病",
    "injured": "受伤",
}

EMOTION_LABELS = {
    "happy": "开心",
    "normal": "平静",
    "sad": "难过",
    "angry": "生气",
    "excited": "兴奋",
}


def _load_config(name: str) -> dict[str, Any]:
    from ..config_loader import load_config

    return load_config(name)


def format_days(days: int) -> str:
    """Render a day count as 天 / 个月 / 年."""
    days = max(0, int(days))
    if days < 30:
        return f"{days}天"
    if days < 365:
        return f"{days // 30}个月{days % 30}天"
    return f"{days // 365}年{(days % 365) // 30}个月"


def format_pet_status(pet, pet_config: dict[str, Any] | None = None) -> str:
    """Full status card for ``/宠物.状态``."""
    from ..core.affection import get_affection_level

    cfg = pet_config if pet_config is not None else _load_config("pet")
    personality = cfg.get("personalities", {}).get(pet.personality, {})
    attributes = pet.attributes or {}

    fields = {
        "name": pet.name,
        "personality": personality.get("name", pet.personality),
        "rarity": RARITY_LABELS.get(pet.rarity, pet.rarity),
        "level": pet.level,
        "exp": pet.exp,
        "exp_next": pet.exp_next,
        "age": format_days(pet.age_days),
        "lifespan": format_days(pet.lifespan_days),
        "health": HEALTH_LABELS.get(pet.health_state, pet.health_state),
        "emotion": EMOTION_LABELS.get(pet.emotion_state, pet.emotion_state),
        "hunger": pet.hunger,
        "mood": pet.mood,
        "fatigue": pet.fatigue,
        "cleanliness": pet.cleanliness,
        "affection": pet.affection,
        "affection_level": get_affection_level(pet.affection),
        "action_points": pet.action_points,
        "hp": attributes.get("hp", 0),
        "atk": attributes.get("atk", 0),
        "def": attributes.get("def", 0),
        "spd": attributes.get("spd", 0),
        "mp": attributes.get("mp", 0),
    }
    title = text("status.title", "🐾 {name} 的状态", name=pet.name)
    body = text("status.body", "", **fields)
    return f"{title}\n{body}" if body else title


def format_daily_report(settle: dict[str, Any]) -> str:
    """Render the outcome of ``core.daily.check_daily_reset``."""
    if not settle.get("need_settle"):
        return text("daily.no_settle", "今天已经结算过了")

    report = settle.get("report") or []
    to_day = report[-1]["day"] if report else settle.get("days_settled", 0)
    lines = [
        text(
            "daily.title",
            "📅 已结算 {days_settled} 天",
            days_settled=settle.get("days_settled", 0),
            to_day=to_day,
        )
    ]
    if settle.get("report_truncated"):
        lines.append(text("daily.truncated", "", kept=len(report)))
    lines.extend(entry.get("log", "") for entry in report)
    lines.append(
        text(
            "daily.action_points",
            "行动点已重置为 {action_points}",
            action_points=settle.get("action_points", 0),
        )
    )

    reincarnation = settle.get("reincarnation")
    if reincarnation and reincarnation.get("can_reincarnate"):
        lines.append(
            text(
                "daily.reincarnation",
                "",
                reasons="、".join(reincarnation.get("reasons", [])),
                points=reincarnation.get("estimated_points", 0),
            )
        )
    return "\n".join(line for line in lines if line)


def format_skill_list(pet, skills_config: dict[str, Any] | None = None) -> str:
    """List the pet's learned skills and their proficiency."""
    cfg = skills_config if skills_config is not None else _load_config("skills")
    limits = cfg.get("limits", {})
    skills = cfg.get("skills", {})
    known = pet.skills or {}

    lines = [
        text(
            "skill.title",
            "📚 {name} 的技能",
            name=pet.name,
            active_used=len(known.get("active", [])),
            active_max=limits.get("active_slots", 0),
            passive_used=len(known.get("passive", [])),
            passive_max=limits.get("passive_slots", 0),
        )
    ]
    entries = []
    for slot in ("active", "passive"):
        for skill_id in known.get(slot, []):
            skill = skills.get(skill_id)
            if skill is None:
                continue
            entries.append(
                text(
                    "skill.line",
                    "",
                    name=skill.get("name", skill_id),
                    type=slot,
                    power=skill.get("power", 0),
                    proficiency=round(pet.skill_proficiency.get(skill_id, 0.0), 2),
                )
            )
    lines.extend(entries or [text("skill.empty", "还没有学会任何技能")])
    return "\n".join(line for line in lines if line)


def format_talent_list(pet, talents_config: dict[str, Any] | None = None) -> str:
    """List the pet's allocated talents."""
    cfg = talents_config if talents_config is not None else _load_config("talents")
    talents = cfg.get("talents", {})

    lines = [
        text(
            "talent.title",
            "✨ {name} 的天赋",
            name=pet.name,
            points=pet.talent_points,
        )
    ]
    entries = []
    for talent_id, level in (pet.talents or {}).items():
        talent = talents.get(talent_id)
        if talent is None:
            continue
        entries.append(
            text(
                "talent.line",
                "",
                name=talent.get("name", talent_id),
                level=level,
                max_level=talent.get("max_level", 0),
            )
        )
    lines.extend(entries or [text("talent.empty", "还没有分配任何天赋")])
    return "\n".join(line for line in lines if line)


def format_biography(bio, name: str = "宠物", limit: int = 10) -> str:
    """Show the newest ``limit`` biography entries plus unlocked titles."""
    entries = list(bio.entries or [])[-max(1, limit) :]
    lines = [
        text("bio.title", "📖 {name} 的传记", name=name, count=len(bio.entries or []))
    ]
    if bio.titles_unlocked:
        lines.append(
            text("bio.titles", "", titles="、".join(bio.titles_unlocked))
        )
    if not entries:
        lines.append(text("bio.empty", "传记还是空的"))
    for entry in entries:
        lines.append(
            text(
                "bio.line",
                "",
                type=entry.get("type", ""),
                content=entry.get("content") or entry.get("title", ""),
            )
        )
    return "\n".join(line for line in lines if line)


def format_daily_logs(bio, limit: int = 10) -> str:
    """Show the newest ``limit`` daily logs."""
    logs = list(bio.daily_logs or [])[-max(1, limit) :]
    lines = [text("logs.title", "🗓 最近的每日日志")]
    if not logs:
        lines.append(text("logs.empty", "还没有日志"))
    for entry in logs:
        lines.append(
            text(
                "logs.line",
                "",
                day=entry.get("day", 0),
                content=entry.get("content", ""),
            )
        )
    return "\n".join(line for line in lines if line)


def format_reincarnation_check(
    check: dict[str, Any],
    reinc=None,
    *,
    pet=None,
) -> str:
    """Render ``/轮回`` — condition report only, never a performed cycle."""
    cycle = (reinc.total_cycles + 1) if reinc is not None else 1
    lines = [text("reincarnation.title", "🔄 轮回", cycle=cycle)]
    if not check.get("can_reincarnate"):
        lines.append(text("reincarnation.unavailable", "当前无法轮回"))
        return "\n".join(line for line in lines if line)

    lines.append(
        text(
            "reincarnation.available",
            "",
            reasons="、".join(check.get("reasons", [])),
            estimated_points=check.get("estimated_points", 0),
            achievement_points=getattr(reinc, "achievement_points", 0),
            spent_points=getattr(reinc, "spent_points", 0),
        )
    )
    lines.append(text("reincarnation.manual_hint", ""))
    return "\n".join(line for line in lines if line)


def format_reincarnation_result(result: dict[str, Any]) -> str:
    """Render the outcome of a confirmed reincarnation."""
    return text("reincarnation.done", "", **result)


def format_perk_list(
    reincarnation_config: dict[str, Any],
    available: int,
) -> str:
    """List the perks exchangeable with achievement points."""
    perks = reincarnation_config.get("perks", {})
    lines = [text("perk.title", "🏅 成就点特权", available=available)]
    if not perks:
        lines.append(text("perk.empty", "暂无可兑换特权"))
    for perk_id, perk in perks.items():
        lines.append(
            text(
                "perk.line",
                "",
                perk_id=perk_id,
                name=perk.get("name", perk_id),
                cost=perk.get("cost", 0),
            )
        )
    return "\n".join(line for line in lines if line)
