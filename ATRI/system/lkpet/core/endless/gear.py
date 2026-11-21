"""无尽模式的装备：4 槽、库存、分解、属性汇总。

数据在 endless_gear.yaml：3 类 × 5 档 × 10 = 150 件。
每件 = 1 主词条 + 1 副词条 + 1 特殊效果。

等阶来源：打怪掉当前最高档；精英/首领掉高一档；商店只刷最高档。

换下来的装备进库存，不销毁；主动分解回收 10% 的金币。
"""

from __future__ import annotations

import random
from typing import Any

SLOTS: tuple[str, ...] = ("weapon", "armor", "accessory")
"""装备槽。武器 1 / 防具 1 / 饰品 2。"""

SLOT_COUNT = {"weapon": 1, "armor": 1, "accessory": 2}
"""每类槽位数量：饰品可以戴两件。"""

DECOMPOSE_RATE = 0.10
"""分解回收率：只回收 10%，让分解是「废物利用」而不是主要收入。"""

PRICE = {1: 20, 2: 40, 3: 80, 4: 160, 5: 320}
"""装备价格：T1-T5。"""


def load_gear_config() -> dict[str, Any]:
    from ...config_loader import load_config

    return load_config("endless_gear")


def gear_list(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    cfg = cfg or load_gear_config()
    return [dict(g) for g in (cfg.get("gear") or [])]


def gear_specs(cfg: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    return {str(g["id"]): g for g in gear_list(cfg)}


def gear_by_slot(slot: str, cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [g for g in gear_list(cfg) if str(g.get("slot")) == slot]


def max_tier_for_stage(stage: int, major_stages: int = 5) -> int:
    """当前大阶段解锁的最高装备档位（每大阶段 +1）。

    入参是 **阶段号**（第几个 10 层），不是层号。层号要先过 floor.stage_of。
    """
    major = (max(1, int(stage)) - 1) // major_stages + 1
    return min(5, major)


def drop_tier(stage: int, *, elite: bool = False, boss: bool = False) -> int:
    """掉落档位：普通取当前最高；精英/首领高一个档。"""
    base = max_tier_for_stage(stage)
    if elite or boss:
        return min(5, base + 1)
    return base


def roll_gear(
    rng: random.Random,
    stage: int,
    slot: str | None = None,
    *,
    elite: bool = False,
    boss: bool = False,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """抽一件装备。

    ``slot`` 为空时**先等概率选槽、再槽内等概率选件**（§7.3）——
    不能直接全池随机：三类件数一旦不等，槽概率就跟着偏。
    """
    if slot is None:
        slot = rng.choice(list(SLOTS))
    tier = drop_tier(stage, elite=elite, boss=boss)
    pool = [
        g
        for g in gear_list(cfg)
        if int(g.get("tier", 0)) == tier and str(g.get("slot")) == slot
    ]
    if not pool:
        return None
    return dict(rng.choice(pool))

    return dict(rng.choice(pool))


def stock_of(run: Any) -> list[str]:
    """本局库存里的装备 id（换下后没销毁的那些）。"""
    return [str(g) for g in (run.pending.get("gear_stock") or [])]


def take_from_stock(run: Any, gear_id: str) -> bool:
    """从库存取出一件穿上（换下旧的，旧的回到库存）。"""
    stock = list(run.pending.get("gear_stock") or [])
    if gear_id not in stock:
        return False
    stock.remove(gear_id)
    equipped, replaced = equip(run.equip, gear_id, None)
    run.equip = equipped
    if replaced:
        stock.append(replaced)
    run.pending["gear_stock"] = stock
    return True


def decompose(run: Any, gear_id: str) -> int:
    """分解库存里的一件，返回回收金币（0 = 没这件）。"""
    stock = list(run.pending.get("gear_stock") or [])
    if gear_id not in stock:
        return 0
    spec = gear_specs().get(gear_id)
    if not spec:
        return 0
    stock.remove(gear_id)
    run.pending["gear_stock"] = stock
    value = decompose_value(spec)
    run.coins += value
    return value


def price_of(spec: dict[str, Any]) -> int:
    return int(PRICE.get(int(spec.get("tier", 1)), 0))


def decompose_value(spec: dict[str, Any]) -> int:
    """分解能换回的金币（价格的 10%）。"""
    return max(0, int(price_of(spec) * DECOMPOSE_RATE))


def gear_effects(spec: dict[str, Any]) -> dict[str, Any]:
    """把主词条与副词条摊平成一份属性字典。"""
    out: dict[str, Any] = {}
    for node in (spec.get("main") or {}, spec.get("sub") or {}):
        out.update(node)
    return out


def equipped_effects(
    equipped: dict[str, list[str]] | None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, float]:
    """``{slot: [gear_id, ...]}`` 的全部属性之和。"""
    specs = gear_specs(cfg)
    total: dict[str, float] = {}
    for ids in (equipped or {}).values():
        for gear_id in ids:
            spec = specs.get(str(gear_id))
            if not spec:
                continue
            for key, value in gear_effects(spec).items():
                if isinstance(value, int | float):
                    total[key] = total.get(key, 0.0) + float(value)
    return total


def validate_equipped(
    equipped: dict[str, list[str]] | None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    """把装备表规整成合法形态：槽位名对得上、每槽不超数量、id 存在。"""
    specs = gear_specs(cfg)
    out: dict[str, list[str]] = {slot: [] for slot in SLOTS}
    for slot, limit in SLOT_COUNT.items():
        for gear_id in list((equipped or {}).get(slot) or [])[:limit]:
            spec = specs.get(str(gear_id))
            if spec and str(spec.get("slot")) == slot:
                out[slot].append(str(gear_id))
    return out


def equip(
    equipped: dict[str, list[str]] | None,
    gear_id: str,
    cfg: dict[str, Any] | None = None,
) -> tuple[dict[str, list[str]], str | None]:
    """装上一件；返回 ``(新装备表, 被换下的 id 或 None)``。

    槽位满了就换掉最早那件（进库存），不自动分解。
    """
    specs = gear_specs(cfg)
    spec = specs.get(str(gear_id))
    current = validate_equipped(equipped, cfg)
    if spec is None:
        return current, None
    slot = str(spec.get("slot"))
    limit = SLOT_COUNT.get(slot, 1)
    replaced: str | None = None
    if len(current[slot]) >= limit:
        replaced = current[slot].pop(0)
    current[slot].append(str(gear_id))
    return current, replaced


def unequip(
    equipped: dict[str, list[str]] | None,
    slot: str,
    gear_id: str | None = None,
    cfg: dict[str, Any] | None = None,
) -> tuple[dict[str, list[str]], str | None]:
    """卸下一件；``gear_id`` 为空时卸该槽第一个。"""
    current = validate_equipped(equipped, cfg)
    ids = current.get(slot) or []
    if not ids:
        return current, None
    target = str(gear_id) if gear_id else ids[0]
    if target not in ids:
        return current, None
    ids.remove(target)
    return current, target


def sell_locked(spec: dict[str, Any]) -> bool:
    """是否禁止分解（目前没有不可分解的装备，保留接口）。"""
    return False
