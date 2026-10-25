"""Affection system: levels, derived bonuses and the shared gain helper.

The affection scale is ``0-1000`` (ten times the original ``0-100`` design) so
that the fractional daily-decay multipliers configured in ``daily.yaml``
(``1.0 / 1.1 / 1.3 / 1.5``) always yield whole numbers.

**好感加成（唯一的加成口径）**：好感越高，经验与战斗输出越高，线性映射、
最多 +10%，并且**按加成后的总值向下取整**（见 :func:`apply_affection_bonus`）。
两个消费者各取所需——``core.leveling`` 拿 ``exp_bonus`` 乘区，
``core.roguelike.battle`` 拿 ``battle_bonus`` 乘区——口径都出自本模块，不各写一份。
"""

import math

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

AFFECTION_BONUS_MAX = 0.10
"""好感带来的加成上限：满好感时经验 / 输出各 +10%。"""


def get_affection_level(affection: int) -> str:
    """Return the band name for ``affection``."""
    for low, high, name in AFFECTION_LEVELS:
        if low <= affection < high:
            return name
    return "灵魂伴侣"


def affection_bonus_ratio(affection: int, cap: float = AFFECTION_BONUS_MAX) -> float:
    """好感折算成加成比例：线性，``0`` 好感 0%、满好感 ``cap``（默认 10%）。

    好感超出量程或为负时先钳到 ``[0, AFFECTION_MAX]``，比例恒在 ``[0, cap]``。
    """
    value = min(AFFECTION_MAX, max(0, int(affection)))
    return (value / AFFECTION_MAX) * cap


def apply_affection_bonus(
    value: int | float,
    affection: int,
    cap: float = AFFECTION_BONUS_MAX,
) -> int:
    """把好感加成叠到 ``value`` 上，**按加成后的总值向下取整**。

    ``value`` 为 100、好感满值时返回 110（+10%）；好感为 0 时原样向下取整，
    与不接这套加成的旧行为一致。加成是「乘区」而不是「平值」，
    所以小数值（如 3 点经验）不会被固定加几点的写法放大。

    取整口径明确为**对总值 floor**，而不是先取整增量再相加——
    后者会丢掉小数部分，让低素质宠物的加成长期为 0。
    """
    return math.floor(float(value) * (1.0 + affection_bonus_ratio(affection, cap)))


def get_affection_bonus(affection: int) -> dict[str, float | bool]:
    """Return the derived modifiers granted by ``affection``.

    ``exp_bonus`` / ``battle_bonus`` 都是**乘区系数**（1.0 = 无加成），
    上限分别为 +30%（沿用历史值，见 ``core.leveling``）与
    :data:`AFFECTION_BONUS_MAX`（+10%，见 ``core.roguelike.battle``）。
    """
    return {
        "exp_bonus": 1.0
        + (min(AFFECTION_MAX, max(0, affection)) / AFFECTION_MAX) * 0.3,
        "battle_bonus": 1.0 + affection_bonus_ratio(affection),
        "unlock_special_skill": affection >= HIGH_AFFECTION,
    }


def add_affection(pet, amount: int) -> None:
    """Raise affection by ``amount``, halving the gain once it is already high."""
    current = pet.affection
    if current >= HIGH_AFFECTION:
        amount = max(1, amount // 2)
    pet.affection = min(AFFECTION_MAX, current + amount)
