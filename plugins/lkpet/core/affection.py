"""Affection system: levels, derived bonuses and the shared gain helper.

The affection scale is ``0-1000`` (ten times the original ``0-100`` design) so
that the fractional daily-decay multipliers configured in ``daily.yaml``
(``1.0 / 1.1 / 1.3 / 1.5``) always yield whole numbers.
"""

AFFECTION_LEVELS = [
    (0, 200, "陌生"),
    (200, 400, "熟悉"),
    (400, 600, "友好"),
    (600, 800, "亲密"),
    (800, 950, "挚爱"),
    (950, 1000, "灵魂伴侣"),
]
"""``(low, high, name)`` bands; ``high`` is exclusive."""

AFFECTION_MAX = 1000
"""Upper bound of the affection scale."""

HIGH_AFFECTION = 800
"""At or above this value further gains are halved."""


def get_affection_level(affection: int) -> str:
    """Return the band name for ``affection``."""
    for low, high, name in AFFECTION_LEVELS:
        if low <= affection < high:
            return name
    return "灵魂伴侣"


def get_affection_bonus(affection: int) -> dict[str, float | bool]:
    """Return the derived modifiers granted by ``affection``."""
    return {
        "exp_bonus": 1.0 + (affection / AFFECTION_MAX) * 0.3,
        "battle_bonus": 1.0 + (affection / AFFECTION_MAX) * 0.1,
        "unlock_special_skill": affection >= HIGH_AFFECTION,
    }


def add_affection(pet, amount: int) -> None:
    """Raise affection by ``amount``, halving the gain once it is already high."""
    current = pet.affection
    if current >= HIGH_AFFECTION:
        amount = max(1, amount // 2)
    pet.affection = min(AFFECTION_MAX, current + amount)
