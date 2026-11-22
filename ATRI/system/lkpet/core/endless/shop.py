"""无尽模式的商店：11 个位置、刷新、购买。

只在每阶段第 9 层出现，与普通战斗二选一。

货架固定 12 个位置：5 遗物 + 3 技能 + 1 武器 + 1 防具 + 2 饰品。
**每类商品的等阶规则不同**：

    遗物  按档位概率表掷（凑套需要全档流通）
    技能  当前已解锁的最高等，且只刷「尚未获得」的
    装备  当前已解锁的最高等

刷新 10 金/次、每次 +10，同一次进店上限 5 次。
买走的位置变空、不补货；金币不足只显示价格不可买。
"""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any

from .gear import gear_by_slot, max_tier_for_stage, price_of
from .relics import relic_specs, roll_tier, tier_available
from .skills import offer_skills, price_of_skill

SLOTS: tuple[tuple[str, int], ...] = (
    ("relic", 5),
    ("skill", 3),
    ("weapon", 1),
    ("armor", 1),
    ("accessory", 2),
)
"""货架的 11 个位置：``[(位置类型, 数量), ...]``。"""

REFRESH_FIRST = 20
REFRESH_STEP = 10
"""刷新成本的兜底默认值；实际读 ``endless.yaml`` 的 ``shop.refresh``。

设计 §9.9 / H5：**无次数上限**，只要付得起就能一直刷，离店重置。
"""


SHELF_SIZE = 12
"""货架总位置数：5 遗物 + 3 技能 + 1 武器 + 1 防具 + 2 饰品。"""

AFFIX_SAME = 0.60
"""刷出「与已有词条相同」的遗物概率；其余 40% 走不同的。"""

RelicPricer = Callable[[dict[str, Any]], int]


RELIC_PRICE = {1: 20, 2: 40, 3: 80, 4: 120, 5: 160}
"""遗物售价（文档 §9.8）。未列出 price 时按档位取这张表。"""

ENDLESS_RELIC_PRICE = {
    1: 100,
    2: 110,
    3: 120,
    4: 130,
    5: 140,
    6: 150,
    7: 160,
    8: 170,
    9: 180,
    10: 200,
}
"""无尽遗物售价（T1–T10）。"""


def default_relic_price(spec: dict[str, Any]) -> int:
    """遗物价格：数据里没写 price 时按档位查表。"""
    if "price" in spec:
        return int(spec.get("price", 0) or 0)
    tier = int(spec.get("tier", 1) or 1)
    if int(spec.get("endless", 0) or 0) or tier > 5:
        return ENDLESS_RELIC_PRICE.get(tier, 200)
    return RELIC_PRICE.get(tier, RELIC_PRICE[1])


def _shop_cfg(name: str, default: Any) -> Any:
    """读 ``endless.yaml`` 的 ``shop.<name>``；缺就退回默认值。"""
    from .config import load_endless_config

    node = (load_endless_config().get("shop") or {}).get(name)
    return default if node is None else node


def refresh_cost(times: int) -> int:
    """第 times+1 次刷新要多少钱。

    **没有次数上限**，价格随次数线性递增（10 / 20 / 30 …）。
    传负数时返回 -1（非法输入，调用方当「刷不了」处理）。
    """
    if times < 0:
        return -1
    node = _shop_cfg("refresh", {})
    first = (
        int(node.get("first", REFRESH_FIRST))
        if isinstance(node, dict)
        else REFRESH_FIRST
    )
    step = (
        int(node.get("step", REFRESH_STEP)) if isinstance(node, dict) else REFRESH_STEP
    )
    return first + step * times


def affix_keys(
    specs: dict[str, dict[str, Any]], owned: dict[str, int] | None
) -> set[str]:
    """已持有遗物的词条集合：effect 的键就是词条。"""
    keys: set[str] = set()
    for relic_id, dup in (owned or {}).items():
        if int(dup) <= 0:
            continue
        spec = specs.get(str(relic_id))
        if not spec:
            continue
        keys.update(str(k) for k in (spec.get("effect") or {}))
    return keys


def weighted_relic_pool(
    tier: int,
    specs: dict[str, dict[str, Any]],
    owned: dict[str, int] | None,
    rng: random.Random,
) -> list[dict[str, Any]]:
    """按词条加权筛候选池：60% 只留同词条，40% 只留异词条。

    某一侧为空时退回全池——否则会出现「筛出空池、这一格刷不出东西」。
    """
    pool = [s for s in specs.values() if int(s.get("tier", 0)) == tier]
    if not pool:
        return []
    mine = affix_keys(specs, owned)
    if not mine:
        return pool
    same: list[dict[str, Any]] = []
    diff: list[dict[str, Any]] = []
    for spec in pool:
        keys = {str(k) for k in (spec.get("effect") or {})}
        (same if keys & mine else diff).append(spec)
    want_same = rng.random() < float(_shop_cfg("affix_bias", AFFIX_SAME))
    chosen = same if want_same else diff
    if not chosen:
        chosen = diff if want_same else same
    return chosen or pool


def pick_relics(
    rng: random.Random,
    stage: int,
    specs: dict[str, dict[str, Any]],
    owned: dict[str, int] | None,
    count: int,
    pricer: RelicPricer,
) -> list[dict[str, Any]]:
    """刷 count 件互不重复的遗物。"""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    guard = 0
    while len(out) < count and guard < 40:
        guard += 1
        tier = roll_tier(rng, stage)
        pool = weighted_relic_pool(tier, specs, owned, rng)
        if not pool:
            if not tier_available(tier, owned):
                break
            continue
        spec = pool[rng.randrange(len(pool))]
        rid = str(spec["id"])
        if rid in seen:
            continue
        seen.add(rid)
        entry = dict(spec)
        entry["price"] = pricer(spec)
        out.append(entry)
    return out


def pick_gear(
    rng: random.Random,
    stage: int,
    slot: str,
    count: int,
    cfg: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """装备只刷当阶最高档，同一次进店不重复。"""
    tier = max_tier_for_stage(stage)
    pool = [g for g in gear_by_slot(slot, cfg) if int(g.get("tier", 0)) == tier]
    if not pool:
        return []
    rng.shuffle(pool)
    return [dict(g) for g in pool[:count]]


def roll_shop(
    rng: random.Random,
    stage: int,
    *,
    owned_relics: dict[str, int] | None = None,
    known_skills: list[str] | None = None,
    cfg: dict[str, Any] | None = None,
    relic_cfg: dict[str, Any] | None = None,
    skill_cfg: dict[str, Any] | None = None,
    gear_cfg: dict[str, Any] | None = None,
    pricer: RelicPricer = default_relic_price,
) -> list[dict[str, Any]]:
    """生成一整排货架。每个位置独立掷；任何一个位置都可能为空。

    空位置保留 ``spec: None``，让界面能画出「这一格没东西」。
    """
    relics = pick_relics(rng, stage, relic_specs(relic_cfg), owned_relics, 5, pricer)
    skills = offer_skills(rng, stage, known_skills, 3, skill_cfg)
    weapons = pick_gear(rng, stage, "weapon", 1, gear_cfg)
    armors = pick_gear(rng, stage, "armor", 1, gear_cfg)
    accs = pick_gear(rng, stage, "accessory", 2, gear_cfg)

    out: list[dict[str, Any]] = []
    for spec in relics:
        out.append({"kind": "relic", "spec": spec, "price": spec["price"]})
    for spec in skills:
        out.append({"kind": "skill", "spec": spec, "price": price_of_skill(spec)})
    for spec in weapons + armors + accs:
        out.append({"kind": "gear", "spec": spec, "price": price_of(spec)})
    while len(out) < SHELF_SIZE:
        out.append({"kind": "empty", "spec": None, "price": 0})
    return out[:SHELF_SIZE]


def buy(
    shelf: list[dict[str, Any]],
    index: int,
    coins: int,
) -> tuple[int, dict[str, Any] | None, str]:
    """买下第 index 个位置；返回 ``(新金币, 买到的条目或 None, 结果码)``。

    结果码：``ok`` / ``no_slot`` / ``empty`` / ``poor``。
    买走后该位置变空、不补货。
    """
    if not 0 <= index < len(shelf):
        return coins, None, "no_slot"
    entry = shelf[index]
    if not entry or not entry.get("spec"):
        return coins, None, "empty"
    price = int(entry.get("price", 0) or 0)
    if coins < price:
        return coins, None, "poor"
    bought = dict(entry)
    shelf[index] = {"kind": "empty", "spec": None, "price": 0}
    return coins - price, bought, "ok"
