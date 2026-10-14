"""Tutorial system: the day-paced onboarding flow.

Progress is stored on the pet (``stats["tutorial_step"]`` /
``stats["tutorial_done"]``) because the task book's ``get_or_create_player`` has
no player table behind it — the pet is the only per-player record the plugin
owns.  Step texts live in ``texts.yaml`` so nothing user-facing is hard-coded.
"""

from typing import Any


def _load_steps() -> list[dict[str, Any]]:
    from ..config_loader import load_config

    return load_config("texts")["tutorial"]["steps"]


def is_started(pet) -> bool:
    """Whether the tutorial was ever started for this pet.

    Pets migrated from the old chat-only plugin have no tutorial state at all;
    they must not be nagged with the "welcome" step.
    """
    return "tutorial_step" in pet.stats


def current_step(pet) -> dict[str, Any] | None:
    """Return the step the player is on.

    ``None`` covers both "already finished" and "never started" (legacy pets).
    """
    if not is_started(pet) or pet.stats.get("tutorial_done"):
        return None
    steps = _load_steps()
    index = int(pet.stats.get("tutorial_step", 0))
    return steps[index] if 0 <= index < len(steps) else None


async def start_tutorial(pet) -> dict[str, Any]:
    """Begin the tutorial for a freshly adopted pet."""
    pet.stats["tutorial_done"] = False
    pet.stats["tutorial_step"] = 0
    return _load_steps()[0]


async def advance_tutorial(pet) -> dict[str, Any] | None:
    """Move to the next step; returns ``None`` when the tutorial just finished."""
    steps = _load_steps()
    index = int(pet.stats.get("tutorial_step", 0)) + 1
    if index >= len(steps):
        pet.stats["tutorial_done"] = True
        return None
    pet.stats["tutorial_step"] = index
    return steps[index]


async def check_tutorial_trigger(pet, event: str) -> dict[str, Any] | None:
    """Advance the tutorial when ``event`` matches the step being waited on."""
    step = current_step(pet)
    if step is None:
        return None
    if step["id"] == event:
        return await advance_tutorial(pet)
    return None


def is_enabled(config) -> bool:
    """Whether the tutorial is switched on in the plugin settings."""
    return bool(getattr(config, "tutorial_enabled", True))
