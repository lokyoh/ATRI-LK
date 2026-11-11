"""Pet status system: per-day decay plus derived health and emotion states.

This module is the single source of truth for status decay and for the health /
emotion verdicts.  ``core.daily`` delegates to it instead of keeping private
copies, so the two can never drift apart.
"""

from typing import Any

STATUS_FIELDS = ("hunger", "mood", "cleanliness", "fatigue", "affection")
"""Status fields decayed once per personal day."""

STATUS_MAX = 100
"""Upper bound of the four care statuses; affection has its own 0-1000 scale."""

_LOW_HUNGER_THRESHOLD = 30
"""Hunger below which the mood decay is amplified."""

_LOW_HUNGER_PENALTY = 2
"""Extra mood loss applied while hunger stays below the threshold."""

_LOW_IS_BAD = ("hunger", "mood", "cleanliness")
"""Care statuses that count as unmet when they drop below their threshold."""


def _load_daily_config() -> dict[str, Any]:
    from ..config_loader import load_config

    return load_config("daily")


def clamp_status(value: int, minimum: int) -> int:
    """Clamp a care status value into ``[minimum, STATUS_MAX]``."""
    return max(minimum, min(STATUS_MAX, value))


def count_unmet_care_needs(pet, affection_config: dict[str, Any]) -> int:
    """Count how many care needs the pet currently fails (0-4).

    ``hunger``/``mood``/``cleanliness`` count as unmet when *below* their
    threshold, ``fatigue`` when *above* its threshold.
    """
    thresholds = affection_config["thresholds"]
    unmet = sum(1 for field in _LOW_IS_BAD if getattr(pet, field) < thresholds[field])
    if pet.fatigue > thresholds["fatigue"]:
        unmet += 1
    return unmet


def affection_decay_amount(pet, daily_config: dict[str, Any] | None = None) -> int:
    """Return the affection lost today for the pet's unmet care needs.

    ``daily.yaml`` defines the ladder: the first unmet need costs the base
    value, and each further unmet need scales it (1.0 / 1.1 / 1.3 / 1.5).  A pet
    whose needs are all met loses nothing.
    """
    cfg = daily_config if daily_config is not None else _load_daily_config()
    affection_cfg = cfg["affection_decay"]
    unmet = count_unmet_care_needs(pet, affection_cfg)
    if unmet == 0:
        return 0
    multipliers = affection_cfg["multipliers"]
    index = min(unmet, len(multipliers)) - 1
    return round(affection_cfg["base"] * multipliers[index])


def apply_daily_decay(
    pet, daily_config: dict[str, Any] | None = None
) -> dict[str, int]:
    """Decay every status by one day and return the *actual* per-field change.

    The returned mapping holds the real deltas after clamping, not the nominal
    values from ``daily.yaml``; a status already sitting on its floor therefore
    reports ``0`` instead of a change that never happened.

    Affection is judged on the state left by this day's decay, matching the task
    book's original ordering (statuses decay, then the verdict is taken).
    """
    daily_cfg = daily_config if daily_config is not None else _load_daily_config()
    decay = daily_cfg["daily_decay"]
    minimums = daily_cfg["status_minimums"]
    before = {field: getattr(pet, field) for field in STATUS_FIELDS}

    pet.hunger = clamp_status(pet.hunger + decay["hunger"], minimums["hunger"])

    mood_change = decay["mood"]
    if pet.hunger < _LOW_HUNGER_THRESHOLD:
        mood_change -= _LOW_HUNGER_PENALTY
    pet.mood = clamp_status(pet.mood + mood_change, minimums["mood"])

    pet.cleanliness = clamp_status(
        pet.cleanliness + decay["cleanliness"], minimums["cleanliness"]
    )
    pet.fatigue = clamp_status(pet.fatigue + decay["fatigue"], minimums["fatigue"])

    loss = affection_decay_amount(pet, daily_cfg)
    if loss:
        pet.affection = max(minimums["affection"], pet.affection - loss)

    return {field: getattr(pet, field) - before[field] for field in STATUS_FIELDS}


def update_health_state(pet) -> None:
    """Derive ``health_state``, checking the most severe condition first.

    ``hunger < 10`` implies ``hunger < 20`` and ``fatigue > 95`` implies
    ``fatigue > 80``, so the task book's original ordering made ``sick`` and
    ``injured`` unreachable.  Severity order keeps all four states reachable.
    """
    if pet.fatigue > 95:
        pet.health_state = "injured"
    elif pet.hunger < 10 or pet.cleanliness < 20:
        pet.health_state = "sick"
    elif pet.hunger < 20 or pet.fatigue > 80:
        pet.health_state = "tired"
    else:
        pet.health_state = "healthy"


def update_emotion_state(pet) -> None:
    """Derive ``emotion_state`` from mood; band names follow the task book."""
    if pet.mood >= 80:
        pet.emotion_state = "happy"
    elif pet.mood >= 50:
        pet.emotion_state = "normal"
    elif pet.mood >= 30:
        pet.emotion_state = "sad"
    elif pet.mood >= 10:
        pet.emotion_state = "angry"
    else:
        pet.emotion_state = "excited"


def get_status_summary(pet) -> dict[str, Any]:
    """Snapshot the decayed status fields plus the derived states."""
    return {
        "mood": pet.mood,
        "hunger": pet.hunger,
        "fatigue": pet.fatigue,
        "cleanliness": pet.cleanliness,
        "affection": pet.affection,
        "health_state": pet.health_state,
        "emotion_state": pet.emotion_state,
        "age_days": pet.age_days,
        "lifespan_days": pet.lifespan_days,
    }
