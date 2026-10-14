"""Biography system: the pet's life record, daily logs, memories and titles.

Only biography-worthy events are stored (see :data:`IMPORTANT_EVENTS`); routine
actions such as feeding are deliberately filtered out so the record stays a
life story rather than an activity log.

Every mutating helper persists explicitly: ``get_or_create_biography`` always
returns a freshly loaded model, so an unsaved change would be silently lost on
the next load.
"""

from datetime import datetime
from typing import Any

MAX_ENTRIES = 1000
"""Oldest biography entries are dropped past this count."""

MAX_DAILY_LOGS = 365
"""Oldest daily logs are dropped past this count."""

IMPORTANT_EVENTS = (
    "milestone",
    "key_choice",
    "battle_victory",
    "first_time",
    "ending",
)
"""Only these event types reach the biography."""

ENDING_EVENT = "ending"
"""An entry of this type also fills ``Biography.ending``."""


def _now_iso() -> str:
    return datetime.now().isoformat()


def _append_entry(bio, entry: dict[str, Any]) -> dict[str, Any]:
    """Append an entry to an already-loaded biography and trim the oldest."""
    entry["timestamp"] = _now_iso()
    bio.entries.append(entry)
    if len(bio.entries) > MAX_ENTRIES:
        bio.entries = bio.entries[-MAX_ENTRIES:]
    if entry.get("type") == ENDING_EVENT:
        bio.ending = entry.get("content")
    return entry


def _append_daily_log(bio, pet, content: str) -> dict[str, Any]:
    """Append one settlement log to an already-loaded biography."""
    log = {"day": pet.age_days, "content": content, "timestamp": _now_iso()}
    bio.daily_logs.append(log)
    if len(bio.daily_logs) > MAX_DAILY_LOGS:
        bio.daily_logs = bio.daily_logs[-MAX_DAILY_LOGS:]
    return log


async def get_or_create_biography(player_id: str | int):
    """Return the player's biography row, creating it with defaults if new."""
    from ..database import Biography, load_biography, save_biography

    bio = load_biography(str(player_id))
    if bio is None:
        bio = Biography(player_id=str(player_id))
        save_biography(bio)
    return bio


async def add_biography_entry(pet, entry: dict[str, Any]) -> None:
    """Append a biography entry and persist it."""
    from ..database import save_biography

    bio = await get_or_create_biography(pet.player_id)
    _append_entry(bio, entry)
    save_biography(bio)


async def add_daily_log(pet, content: str) -> None:
    """Append the log of one settled day and persist it."""
    from ..database import save_biography

    bio = await get_or_create_biography(pet.player_id)
    _append_daily_log(bio, pet, content)
    save_biography(bio)


async def add_memory(pet, memory: dict[str, Any]) -> None:
    """Record a memory and persist it."""
    from ..database import save_biography

    bio = await get_or_create_biography(pet.player_id)
    memory["timestamp"] = _now_iso()
    bio.memories.append(memory)
    save_biography(bio)


async def unlock_title(pet, title: str) -> bool:
    """Grant a title once; a repeat grant is a no-op returning ``False``.

    The title and its milestone entry are written on the *same* loaded biography
    and saved once, so neither change can overwrite the other.
    """
    from ..database import save_biography

    bio = await get_or_create_biography(pet.player_id)
    if title in bio.titles_unlocked:
        return False

    bio.titles_unlocked.append(title)
    _append_entry(
        bio,
        {
            "type": "milestone",
            "title": f"获得称号：{title}",
            "content": f"宠物获得了「{title}」的称号",
        },
    )
    save_biography(bio)
    return True


async def maybe_add_biography(pet, event_type: str, content: str) -> None:
    """Record ``content`` only when ``event_type`` is biography-worthy."""
    if event_type in IMPORTANT_EVENTS:
        await add_biography_entry(pet, {"type": event_type, "content": content})
