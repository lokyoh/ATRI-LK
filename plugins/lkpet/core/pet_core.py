"""Pet core: creating, loading and persisting a player's single pet.

The task book never specified this module by name, but every handler in §8 calls
``pet_core.create_pet`` / ``pet_core.get_pet`` / ``pet_core.save_pet``, so it is
the bridge between the handlers and ``database.py``.
"""

import random
from datetime import datetime
from typing import Any

from .attributes import ATTRIBUTE_KEYS, calc_attributes_on_level_up
from .leveling import calc_exp_next

QUALIFICATION_RANGE = (0.9, 1.1)
"""Per-attribute 资质 multiplier rolled once at adoption.

``core.attributes`` multiplies growth by ``qualification``; the task book only
says the pet differs by 性格与属性, without defining how 资质 is rolled.
"""

NAME_MAX_LENGTH = 10
"""Adopted from the legacy adoption flow's validation."""

INSTRUCTION_MAX_LENGTH = 500


def _load_pet_config() -> dict[str, Any]:
    from ..config_loader import load_config

    return load_config("pet")


def _roll_qualification() -> dict[str, float]:
    low, high = QUALIFICATION_RANGE
    return {key: round(random.uniform(low, high), 2) for key in ATTRIBUTE_KEYS}


def ensure_initialized(pet, pet_config: dict[str, Any] | None = None) -> bool:
    """Fill in the fields legacy rows leave empty; returns True when changed.

    Pets migrated from the old chat-only plugin have ``ATTRIBUTES`` /``QUALIFICATION``
    at their ``'{}'`` column default, so they would show HP 0 and never gain
    anything on level-up until something recomputes them.

    Level-appropriate skills are unlocked here too: ``core.leveling`` only calls
    ``check_skill_unlock`` on a level-up, so a pet that never levels up would sit
    at level 1 without its ``unlock_condition: {level: 1}`` starting skill.
    """
    from .skills import check_skill_unlock

    changed = False
    if not pet.qualification:
        pet.qualification = _roll_qualification()
        changed = True
    if not pet.attributes:
        cfg = pet_config if pet_config is not None else _load_pet_config()
        pet.attributes = calc_attributes_on_level_up(pet, cfg)
        changed = True
    if check_skill_unlock(pet):
        changed = True
    return changed


async def get_pet(player_id: str | int):
    """Return the player's pet, or ``None`` when they never adopted one.

    Legacy rows are normalised (and persisted) on first read.
    """
    from ..database import PET_KEY_COLUMN, Pet, load_model, pet_table

    pet = load_model(pet_table, Pet, PET_KEY_COLUMN, str(player_id))
    if pet is not None and ensure_initialized(pet):
        await save_pet(pet)
    return pet


async def save_pet(pet) -> None:
    """Persist the pet (insert or update)."""
    from ..database import PET_KEY_COLUMN, pet_table, save_model

    save_model(pet_table, pet, PET_KEY_COLUMN)


async def create_pet(
    player_id: str | int,
    personality: str,
    name: str,
    *,
    instruction: str = "",
    pet_config: dict[str, Any] | None = None,
):
    """Adopt the player's one and only pet.

    A player may never own a second pet, so an existing row is an error rather
    than a replacement.
    """
    from ..database import Pet

    cfg = pet_config if pet_config is not None else _load_pet_config()
    if personality not in cfg.get("personalities", {}):
        raise ValueError("无效的性格")

    existing = await get_pet(player_id)
    if existing is not None:
        raise ValueError("你已经有一只宠物了，每人只能有一只")

    now = datetime.now()
    pet = Pet(
        player_id=str(player_id),
        name=name,
        instruction=instruction,
        personality=personality,
        qualification=_roll_qualification(),
        adopted_at=now,
        cycle_started_at=now,
        last_settlement_at=now,
        lifespan_days=cfg.get("default_lifespan_days", 120),
    )
    ensure_initialized(pet, cfg)
    pet.exp_next = calc_exp_next(pet.level)
    await save_pet(pet)
    return pet


async def rename_pet(pet, new_name: str) -> tuple[str, str]:
    """Rename the pet and persist it; returns ``(old_name, new_name)``."""
    old_name = pet.name
    pet.name = new_name
    await save_pet(pet)
    return old_name, new_name


async def set_instruction(pet, instruction: str) -> None:
    """Replace the pet's 设定 (used as the chat persona) and persist it."""
    pet.instruction = instruction[:INSTRUCTION_MAX_LENGTH]
    await save_pet(pet)


def clean_and_validate_name(raw: str) -> str:
    """Clean a player-supplied pet name, raising ``ValueError`` when invalid.

    Reuses the framework's helpers so the rules match the rest of the bot
    (same cleaning and the same 违禁词 / bot-name rejection as user nicknames).
    """
    from ATRI.system.lkapi.bot import util as lk_util

    name = lk_util.clean_str(raw or "")
    if not name:
        raise ValueError("名字为空哦")
    if len(name) > NAME_MAX_LENGTH:
        raise ValueError(f"名字不能超过 {NAME_MAX_LENGTH} 个字符")
    if name in lk_util.bot_names:
        raise ValueError("讨厌，不能使用咱的名字哦")
    if not lk_util.is_valid_name(name):
        raise ValueError("这个名字不太合适哦")
    return name


def clean_instruction(raw: str) -> str:
    """Light sanitising kept from the legacy 设定 command."""
    return (raw or "").replace("'", "").strip()


def personality_display(
    personality: str,
    pet_config: dict[str, Any] | None = None,
) -> str:
    """Chinese display name for a personality key (falls back to the key)."""
    cfg = pet_config if pet_config is not None else _load_pet_config()
    item = cfg.get("personalities", {}).get(personality) or {}
    return str(item.get("name") or personality)


def describe_personalities(pet_config: dict[str, Any] | None = None) -> list[str]:
    """Selectable personalities as Chinese names, for usage messages."""
    cfg = pet_config if pet_config is not None else _load_pet_config()
    return [
        str(item.get("name") or key)
        for key, item in cfg.get("personalities", {}).items()
    ]


def resolve_personality(
    value: str,
    pet_config: dict[str, Any] | None = None,
) -> str:
    """Map a player-supplied personality to its config key.

    Accepts both the Chinese name (``勇敢``) and the English key (``brave``),
    case-insensitively, so the usage text can advertise Chinese while scripts
    and the tutorial can keep using keys.
    """
    cfg = pet_config if pet_config is not None else _load_pet_config()
    personalities = cfg.get("personalities", {})
    wanted = (value or "").strip()
    options = " / ".join(describe_personalities(cfg))

    if not wanted:
        raise ValueError(f"请选择性格：{options}")

    lowered = wanted.lower()
    for key, item in personalities.items():
        candidates = {str(key).lower(), str(item.get("name") or "").strip().lower()}
        if lowered in candidates:
            return key

    raise ValueError(f"无效的性格：{wanted}（可选：{options}）")
