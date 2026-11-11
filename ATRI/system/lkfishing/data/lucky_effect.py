from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FishLuckyTier:
    """钓鱼侧的幸运档位。"""

    name: str
    """档位名称。"""

    min_lucky: int
    """该档位的幸运值下限。"""

    max_lucky: int
    """该档位的幸运值上限。"""

    fish_chance: float
    """钓鱼出鱼率修正。"""

    fish_quality: int
    """钓鱼品质修正，直接累加到品质判定值上。"""


FISH_LUCKY_TIERS: list[FishLuckyTier] = [
    FishLuckyTier("大吉", 80, 100, 0.05, 5),
    FishLuckyTier("吉", 40, 79, 0.025, 2),
    FishLuckyTier("平", -34, 39, 0.0, 0),
    FishLuckyTier("小凶", -89, -35, -0.025, -2),
    FishLuckyTier("凶", -100, -90, -0.05, -5),
]
"""全部档位"""


def get_lucky_tier(lucky_level: int) -> FishLuckyTier:
    """获取幸运值所在的档位。"""
    if lucky_level < 0 or lucky_level > len(FISH_LUCKY_TIERS):
        raise ValueError("不存在的挡位")
    return FISH_LUCKY_TIERS[lucky_level]


def get_fish_chance_bonus(lucky_level: int) -> float:
    """获取钓鱼出鱼率的修正量。"""
    return get_lucky_tier(lucky_level).fish_chance


def get_fish_quality_bonus(lucky_level: int) -> int:
    """获取钓鱼品质判定值的修正量。"""
    return get_lucky_tier(lucky_level).fish_quality
