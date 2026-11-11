"""幸运值(运气)系统。

幸运值每日重置为 ``-100`` ~ ``100`` 的随机值，并按档位影响：

- 农场收获的品质判定(银/金/铱)
- 农场额外产物(``harvest_list`` 中带 ``+`` 的项)的掉落概率
- 钓鱼的出鱼率与鱼类品质

档位区间与数值见 :data:`LUCKY_TIERS`。
"""

from __future__ import annotations

from dataclasses import dataclass

MIN_LUCKY = -100
"""幸运值下限。"""

MAX_LUCKY = 100
"""幸运值上限。"""


@dataclass(frozen=True)
class LuckyTier:
    """幸运档位。"""

    level: int
    """挡位等级。"""

    name: str
    """档位名称。"""

    min_lucky: int
    """该档位的幸运值下限。"""

    max_lucky: int
    """该档位的幸运值上限。"""

    quality: int
    """收获品质修正，直接累加到品质判定值上。"""

    drop_rate: float
    """额外产物掉落概率倍率。"""


LUCKY_TIERS: tuple[LuckyTier, ...] = (
    LuckyTier(0, "大吉", 80, 100, 20, 1.2),
    LuckyTier(1, "吉", 40, 79, 10, 1.1),
    LuckyTier(2, "平", -34, 39, 0, 1.0),
    LuckyTier(3, "小凶", -89, -35, -5, 0.95),
    LuckyTier(4, "凶", -100, -90, -10, 0.9),
)
"""全部幸运档位，按幸运值从高到低排列。"""

NEUTRAL_TIER: LuckyTier = LUCKY_TIERS[2]
"""平档，未能匹配任何区间时的兜底档位。"""


def clamp_lucky(lucky: int) -> int:
    """将幸运值限制到合法范围内。"""
    if lucky > MAX_LUCKY:
        return MAX_LUCKY
    if lucky < MIN_LUCKY:
        return MIN_LUCKY
    return lucky


def get_lucky_tier(lucky: int) -> LuckyTier:
    """获取幸运值所在的档位。"""
    lucky = clamp_lucky(int(lucky))
    for tier in LUCKY_TIERS:
        if tier.min_lucky <= lucky <= tier.max_lucky:
            return tier
    return NEUTRAL_TIER


def get_tier_name(lucky: int) -> str:
    """获取幸运值所在的档位名称。"""
    return get_lucky_tier(lucky).name


def get_quality_bonus(lucky: int) -> int:
    """获取收获品质判定值的修正量。"""
    return get_lucky_tier(lucky).quality


def get_drop_chance(chance: int, lucky: int) -> int:
    """按幸运值缩放额外产物的掉落概率。

    概率为 ``100`` 的项属于保底产出，不参与缩放；缩放结果向下取整，
    但原概率大于 ``0`` 时至少保留 ``1``。
    """
    if chance <= 0 or chance >= 100:
        return chance
    result = int(chance * get_lucky_tier(lucky).drop_rate)
    if result < 1:
        result = 1
    return result


def format_lucky(lucky: int) -> str:
    """格式化幸运值，形如 ``幸运: 42 (吉)``。"""
    lucky = clamp_lucky(int(lucky))
    return f"幸运: {lucky} ({get_tier_name(lucky)})"
