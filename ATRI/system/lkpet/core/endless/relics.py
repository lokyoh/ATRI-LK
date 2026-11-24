"""无尽模式的遗物：加载、抽取、套装、效果汇总。

数据在 endless_relics.yaml：27 套 × 8 件 = 216 套装件 + 100 散件。
散件固定 T1 30 / T2 25 / T3 20 / T4 15 / T5 10。

**池子没有总数上限**：同一件可以重复获得并叠加，只受档位概率支配。
散件的 set 为空，不参与套装计数。

套装两档：5 件与 8 件，累积生效。
"""

from __future__ import annotations

import random
from typing import Any

TIERS = (1, 2, 3, 4, 5)
"""遗物档位。"""

SET_FULL = 8
SET_HALF = 5
"""套装两档的件数门槛。"""

T1_FLOOR = 0.10
"""T1 的下限：再高层的掉落也不会把最低档压到 10% 以下。"""

SQUEEZE_FLOOR = 0.10
"""T2 / T3 被挤时的下限。"""

HALF_WEIGHT = 1.0 / 3.0
FULL_WEIGHT = 2.0 / 3.0
"""5 件给该属性满值的 1/3，8 件再给 2/3（合计 1 份）。"""


def load_relics_config() -> dict[str, Any]:
    from ...config_loader import load_config

    return load_config("endless_relics")


def set_meta(cfg: dict[str, Any] | None = None) -> dict[str, dict[str, str]]:
    cfg = cfg or load_relics_config()
    raw = cfg.get("sets") or {}
    return {str(k): dict(v) for k, v in raw.items()}


def relic_list(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    cfg = cfg or load_relics_config()
    return [dict(r) for r in (cfg.get("relics") or [])]


def relic_specs(cfg: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    return {str(r["id"]): r for r in relic_list(cfg)}


def relics_by_tier(
    tier: int, cfg: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    return [r for r in relic_list(cfg) if int(r.get("tier", 0)) == int(tier)]


def relic_max_tier(stage: int) -> int:
    """该阶段可获得的最高遗物档位。

    与文档 §9.3 的档位概率表同源：表里阶段 1 就有 T2、阶段 2 解锁 T3、
    阶段 3 解锁 T4、阶段 4 解锁 T5，即 ``min(5, stage + 1)``。
    """
    return min(TIERS[-1], max(1, int(stage)) + 1)


def tier_weights(stage: int) -> dict[int, float]:
    """该阶段各档位的产出概率。

    T2-T5 各自从解锁阶段起步，每阶段 +1 个百分点，涨到 40% 就停；
    T1 是 100 减其余。T1 触底 10% 后转为「挤」：从 T2 起依次扣。
    """
    caps = {2: 0.40, 3: 0.40, 4: 0.40, 5: 0.40}
    unlock = {2: 1, 3: 2, 4: 3, 5: 4}
    start = {2: 0.20, 3: 0.15, 4: 0.10, 5: 0.05}
    weights: dict[int, float] = {1: 0.0}
    for tier in (2, 3, 4, 5):
        if stage < unlock[tier]:
            weights[tier] = 0.0
            continue
        steps = stage - unlock[tier]
        weights[tier] = min(caps[tier], start[tier] + 0.01 * steps)
    rest = 1.0 - sum(weights.values())
    if rest >= T1_FLOOR:
        weights[1] = rest
        return weights
    # T1 触底后转为「挤」：从 T2 起依次扣，T2/T3 各留 10% 下限
    weights[1] = T1_FLOOR
    overflow = T1_FLOOR - rest
    for tier in (2, 3, 4, 5):
        if overflow <= 0:
            break
        floor = SQUEEZE_FLOOR if tier in (2, 3) else 0.0
        room = max(0.0, weights[tier] - floor)
        cut = min(room, overflow)
        weights[tier] -= cut
        overflow -= cut
    return weights


def owned_count(
    owned: dict[str, int] | None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, int]:
    """``{套装 key: 已持有件数}``；散件不计。"""
    specs = relic_specs(cfg)
    counts: dict[str, int] = {}
    for relic_id, dup in (owned or {}).items():
        spec = specs.get(str(relic_id))
        if not spec:
            continue
        key = str(spec.get("set") or "")
        if not key:
            continue
        counts[key] = counts.get(key, 0) + max(0, int(dup))
    return counts


def roll_tier(rng: random.Random, stage: int) -> int:
    """按阶段权重掷一个档位，结果不超过本阶段解锁的最高档。"""
    cap = relic_max_tier(stage)
    weights = tier_weights(stage)
    total = sum(weights.values()) or 1.0
    roll = rng.uniform(0, total)
    acc = 0.0
    for tier in TIERS:
        if tier > cap:
            break
        acc += weights.get(tier, 0.0)
        if roll <= acc:
            return tier
    return 1


def tier_available(
    tier: int,
    owned: dict[str, int] | None,
    cfg: dict[str, Any] | None = None,
) -> bool:
    """该档是否有可抽的遗物。

    池子**没有总数上限**：同一件可以重复获得并叠加，所以只看该档有没有数据，
    不看玩家是否已经拿过。
    """
    return bool(relics_by_tier(tier, cfg))


def fallback_tier(
    tier: int,
    owned: dict[str, int] | None,
    cfg: dict[str, Any] | None = None,
    cap: int | None = None,
) -> int | None:
    """降级优先的回落：先往低级找，低级全空才往高级；全空返回 None。"""
    if tier_available(tier, owned, cfg):
        return tier
    for lower in range(tier - 1, 0, -1):
        if tier_available(lower, owned, cfg):
            return lower
    limit = min(6, (cap + 1) if cap else 6)
    for higher in range(tier + 1, limit):
        if tier_available(higher, owned, cfg):
            return higher
    return None


def pick_relic(
    rng: random.Random,
    stage: int,
    owned: dict[str, int] | None = None,
    cfg: dict[str, Any] | None = None,
    tier: int | None = None,
) -> dict[str, Any] | None:
    """掷档 → 降级回落 → 从该档未持有的遗物里随机取一件。

    全池抽空时返回 None（调用方转无尽遗物）。
    """
    cap = relic_max_tier(stage)
    tier = int(tier) if tier is not None else roll_tier(rng, stage)
    tier = min(tier, cap)
    # 池子没有总数上限：重复件按叠加计，所以不过滤已持有
    pool = relics_by_tier(fallback_tier(tier, owned, cfg, cap=cap) or tier, cfg)
    if not pool:
        # 普通遗物池全空 → 转无尽遗物（§9.5 / §9.6）
        return roll_endless_relic(rng, stage, owned=owned)
    return dict(rng.choice(pool))


ENDLESS_STATS = ("atk", "def", "hp", "mp")
"""无尽遗物的四维（§9.5）。"""

ENDLESS_CAP_PER_MAJOR = 3
"""每 3 个大阶段提升 1 级上限。"""


def endless_cap(stage: int) -> int:
    """该阶段无尽遗物的档位上限（T1–T10）。

    §9.5：**每 3 个大阶段**（= 15 个普通阶段）提升 1 级。
    """
    major = (max(1, int(stage)) - 1) // 5 + 1
    return min(10, 1 + (major - 1) // ENDLESS_CAP_PER_MAJOR + 0)


def roll_endless_relic(
    rng: random.Random, stage: int, *, owned: dict[str, int] | None = None
) -> dict[str, Any]:
    """池空后转出的无尽遗物：上限档 × 四维，等概率组合（§9.5）。"""
    cap = endless_cap(stage)
    tier = rng.randint(1, cap)
    stat = rng.choice(list(ENDLESS_STATS))
    # 数值按档位线性放大；四维同价，所以只改数值不改键
    value = {"atk": 2 * tier, "def": 2 * tier, "hp": 12 * tier, "mp": 8 * tier}[stat]
    return {
        "id": f"endless_{tier}_{stat}",
        "tier": tier,
        "endless": 1,
        "name": f"无尽·{stat}·T{tier}",
        "effect": {stat: value},
    }


def offer_relics(
    rng: random.Random,
    stage: int,
    owned: dict[str, int] | None = None,
    count: int = 3,
    cfg: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """三选一：抽 count 件互不重复的遗物。"""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    guard = 0
    while len(out) < count and guard < 80:
        guard += 1
        spec = pick_relic(rng, stage, owned, cfg)
        if spec is None:
            break
        relic_id = str(spec["id"])
        if relic_id in seen:
            continue
        seen.add(relic_id)
        out.append(spec)
    return out


EVENT_RELICS_CFG = "endless_event_relics"
"""事件遗物独立池（设计 §6.3）：无档位、只能由事件获得、不计套装。"""


def event_relic_list(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """事件遗物清单；配置文件缺失时返回空表。"""
    from ...config_loader import load_config

    try:
        node = load_config(EVENT_RELICS_CFG)
    except FileNotFoundError:
        return []
    return [dict(r) for r in (node.get("relics") or [])]


def event_relic_specs(cfg: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    return {str(r["id"]): r for r in event_relic_list(cfg)}


def all_relic_specs(cfg: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """普通遗物 + 事件遗物合并查表：面板/查看要能查到两者。"""
    out = relic_specs(cfg)
    for relic_id, spec in event_relic_specs().items():
        out.setdefault(relic_id, spec)
    return out


def is_cursed_relic(spec: dict[str, Any]) -> bool:
    """这件是否带代价：有负属性，或特殊效果是损耗型的。"""
    for value in (spec.get("effect") or {}).values():
        if isinstance(value, int | float) and value < 0:
            return True
    node = spec.get("special") or {}
    return str(node.get("id") or "") in DETRIMENTAL_SPECIALS


DETRIMENTAL_SPECIALS = frozenset(
    (
        "bleed_self",
        "mp_drain",
        "taken_up",
    )
)
"""代价型特殊效果：诅咒只在这些里抽。"""


def roll_event_relic(
    rng: random.Random,
    owned: dict[str, int] | None = None,
    *,
    cfg: dict[str, Any] | None = None,
    cursed_only: bool = False,
) -> dict[str, Any] | None:
    """按权重抽一件本局还没有的事件遗物；池空返回 None。"""
    owned_ids = {str(k) for k in (owned or {})}
    pool = [
        r
        for r in event_relic_list(cfg)
        if str(r.get("id")) not in owned_ids and (not cursed_only or is_cursed_relic(r))
    ]
    if not pool:
        return None
    weights = [float(r.get("weight", 1) or 1) for r in pool]
    total = sum(weights)
    if total <= 0:
        return dict(rng.choice(pool))
    point = rng.uniform(0, total)
    acc = 0.0
    for spec, weight in zip(pool, weights, strict=True):
        acc += weight
        if point <= acc:
            return dict(spec)
    return dict(pool[-1])


def is_event_relic(relic_id: str, cfg: dict[str, Any] | None = None) -> bool:
    return str(relic_id) in event_relic_specs(cfg)


def relic_attr_effects(
    owned: dict[str, int] | None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, float]:
    """事件遗物的属性部分（含负值）。"""
    specs = event_relic_specs(cfg)
    total: dict[str, float] = {}
    for relic_id, dup in (owned or {}).items():
        spec = specs.get(str(relic_id))
        if not spec:
            continue
        times = max(0, int(dup))
        for key, value in (spec.get("effect") or {}).items():
            if isinstance(value, int | float) and value:
                total[str(key)] = total.get(str(key), 0.0) + float(value) * times
    return total


def active_specials(
    owned: dict[str, int] | None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, float]:
    """本局生效的触发式特殊效果 {id: value}，同类取最大值。"""
    specs = event_relic_specs(cfg)
    out: dict[str, float] = {}
    for relic_id, dup in (owned or {}).items():
        if int(dup) <= 0:
            continue
        spec = specs.get(str(relic_id))
        if not spec:
            continue
        node = spec.get("special") or {}
        key = str(node.get("id") or "")
        if not key:
            continue
        value = float(node.get("value", 1.0) or 1.0)
        out[key] = max(out.get(key, 0.0), value)
    return out


def relic_effect(spec: dict[str, Any]) -> dict[str, Any]:
    """把一条遗物的 effect 摊平成属性键。"""
    return dict(spec.get("effect") or {})


def total_stat_bonuses(
    owned: dict[str, int] | None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, float]:
    """本局所有遗物的平值加成之和（不含套装）。"""
    specs = relic_specs(cfg)
    total: dict[str, float] = {}
    for relic_id, dup in (owned or {}).items():
        spec = specs.get(str(relic_id))
        if not spec:
            continue
        times = max(0, int(dup))
        for key, value in relic_effect(spec).items():
            if isinstance(value, dict):
                continue
            total[key] = total.get(key, 0.0) + float(value) * times
    return total


def _set_full_value(
    set_key: str,
    specs: dict[str, dict[str, Any]],
) -> float:
    """该套的满值：取该套 T5 件的效果值。"""
    for spec in specs.values():
        if str(spec.get("set") or "") != set_key:
            continue
        if int(spec.get("tier", 0)) != 5:
            continue
        for value in relic_effect(spec).values():
            if isinstance(value, dict):
                continue
            return float(value)
    return 0.0


def set_bonuses(
    owned: dict[str, int] | None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, float]:
    """套装档位的额外加成。

    每档给的是「该属性一份满值」的比例：5 件给 1/3、8 件再给 2/3。
    满值取该套 T5 件的数值，所以满 8 件 ≈ 白送一件 T5。
    """
    specs = relic_specs(cfg)
    counts = owned_count(owned, cfg)
    total: dict[str, float] = {}
    for key, count in counts.items():
        if count < SET_HALF:
            continue
        value = _set_full_value(key, specs)
        if value <= 0:
            continue
        ratio = HALF_WEIGHT + (FULL_WEIGHT if count >= SET_FULL else 0.0)
        total[key] = total.get(key, 0.0) + value * ratio
    return total


def is_set_complete(
    owned: dict[str, int] | None,
    set_key: str,
    cfg: dict[str, Any] | None = None,
) -> bool:
    return owned_count(owned, cfg).get(set_key, 0) >= SET_FULL


def describe_sets(
    owned: dict[str, int] | None,
    cfg: dict[str, Any] | None = None,
) -> list[tuple[str, int, str]]:
    """``[(套装 key, 已持有, 显示名), ...]``，只列已持有的。"""
    counts = owned_count(owned, cfg)
    meta = set_meta(cfg)
    out: list[tuple[str, int, str]] = []
    for key, count in sorted(counts.items()):
        name = str((meta.get(key) or {}).get("name") or key)
        out.append((key, count, name))
    return out
