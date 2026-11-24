"""无尽模式的楼层结构：阶段、层号、选项池、区域。

每阶段 10 层，层内偏移决定内容：

    1      固定普通战斗（无选择）
    2-8    1~3 个选项（10% / 75% / 15%）
    9      普通战斗 与 商店 二选一
    10     精英；每 5 阶段的层 10 为首领

区域每阶段随机一个，本阶段 10 层全部使用该区域的怪。
"""

from __future__ import annotations

import random
from typing import Any

from .config import load_endless_config

STAGE_FLOORS = 10
"""每阶段的层数。"""

MAJOR_STAGES = 5
"""每个大阶段的阶段数。"""

REGIONS: tuple[str, ...] = ("plains", "forest", "lava", "frost", "abyss")
"""5 个区域，沿用爬塔的名字。"""

REGION_CN = {
    "plains": "青草平原",
    "forest": "迷雾森林",
    "lava": "熔岩洞窟",
    "frost": "冰封王座",
    "abyss": "深渊之巅",
}

REGION_ELEMENTS: dict[str, tuple[str, ...]] = {
    "plains": ("wind", "earth", "light"),
    "forest": ("earth", "dark", "wind"),
    "lava": ("fire", "earth", "dark"),
    "frost": ("ice", "wind", "thunder"),
    "abyss": ("dark", "thunder", "light"),
}
"""每区域 3 种主打元素：单一元素套装在该区域至少有一半有效。"""


def stage_of(floor: int) -> int:
    """层号 → 阶段号（从 1 起）。"""
    return max(1, (int(floor) - 1) // STAGE_FLOORS + 1)


def offset_of(floor: int) -> int:
    """层号 → 阶段内偏移（1~10）。"""
    return (int(floor) - 1) % STAGE_FLOORS + 1


def major_stage_of(floor: int) -> int:
    """层号 → 大阶段号（每 5 阶段一个）。"""
    return (stage_of(floor) - 1) // MAJOR_STAGES + 1


def is_boss_floor(floor: int) -> bool:
    """首领层：阶段内第 10 层，且阶段号是 5 的倍数。"""
    return offset_of(floor) == STAGE_FLOORS and stage_of(floor) % MAJOR_STAGES == 0


def is_elite_floor(floor: int) -> bool:
    """精英层：阶段内第 10 层，且不是首领层。"""
    return offset_of(floor) == STAGE_FLOORS and not is_boss_floor(floor)


def floor_kind(floor: int) -> str:
    """层类型：``fixed`` / ``choice`` / ``shop_gate`` / ``elite`` / ``boss``。"""
    offset = offset_of(floor)
    if offset == 1:
        return "fixed"
    if offset == STAGE_FLOORS:
        return "boss" if is_boss_floor(floor) else "elite"
    if offset == STAGE_FLOORS - 1:
        return "shop_gate"
    return "choice"


def region_of(floor: int, seed: int, cfg: dict[str, Any] | None = None) -> str:
    """该层所属区域：整阶段共用一次抽取结果。

    **不复用爬塔的 region_of**：那个按绝对层号查表，越界钳到尾区域。
    这里按阶段号重掷，同一阶段内恒定。
    """
    stage = stage_of(floor)
    rng = random.Random(f"{seed}:{stage}:region")
    return rng.choice(list(REGIONS))


def option_count(floor: int, seed: int, cfg: dict[str, Any] | None = None) -> int:
    """该层的选项数（1~3）。固定概率，不随层数变化。"""
    cfg = cfg or load_endless_config()
    weights = (cfg.get("stage") or {}).get("option_weights") or {}
    if not weights:
        return 2
    picks = {int(k): int(v) for k, v in weights.items()}
    total = sum(picks.values()) or 1
    rng = random.Random(f"{seed}:{floor}:option_count")
    roll = rng.uniform(0, total)
    acc = 0.0
    for count in sorted(picks):
        acc += picks[count]
        if roll <= acc:
            return count
    return max(picks)


def secondary_pool(cfg: dict[str, Any] | None = None) -> dict[str, int]:
    """普通层第二个选项的权重表（事件 / 强化战斗 / 休息 / 群怪）。"""
    cfg = cfg or load_endless_config()
    raw = (cfg.get("stage") or {}).get("pool_weights") or {}
    return {str(k): int(v) for k, v in raw.items()}


def pick_secondary(floor: int, seed: int, cfg: dict[str, Any] | None = None) -> str:
    """按权重抽一个非战斗内容。"""
    weights = secondary_pool(cfg)
    if not weights:
        return "event"
    total = sum(weights.values()) or 1
    rng = random.Random(f"{seed}:{floor}:secondary")
    roll = rng.uniform(0, total)
    acc = 0.0
    for kind, weight in sorted(weights.items()):
        acc += weight
        if roll <= acc:
            return kind
    return "event"


def build_options(
    floor: int, seed: int, cfg: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """构造该层的选项列表。

    返回 ``[{"kind": ..., ...}, ...]``；层类型决定数量与内容：

    * ``fixed``     只有一个普通战斗
    * ``choice``    1~3 个，第一个始终是普通战斗，其余按权重抽
    * ``shop_gate`` 只有战斗与商店两者
    * ``elite`` / ``boss`` 只有一个战斗
    """
    kind = floor_kind(floor)
    if kind == "shop_gate":
        return [{"kind": "battle"}, {"kind": "shop"}]
    if kind in ("fixed", "elite", "boss"):
        return [{"kind": "battle"}]
    count = option_count(floor, seed, cfg)
    opts: list[dict[str, Any]] = [{"kind": "battle"}]
    taken = {str(opts[0]["kind"])}
    for index in range(1, count):
        # 同一层的选项不能重复：两个「事件」并列只是把同一件事印两遍。
        kind = pick_secondary(floor, seed + index, cfg)
        for retry in range(1, len(secondary_pool(cfg)) + 1):
            if kind not in taken:
                break
            kind = pick_secondary(floor, seed + index + retry * 97, cfg)
        opt: dict[str, Any] = {"kind": kind}
        if kind == "packs":
            # 3 只随机怪 + 2 倍奖励（§11.11 的「群怪」层选项）
            opt["members"] = 3
            opt["reward_mult"] = 2.0
        opts.append(opt)
        taken.add(kind)
    return opts
