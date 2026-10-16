"""遗物系统：图鉴、效果、乘区、掉落与授予。"""

from datetime import datetime
from typing import Any

from ._common import (
    _load_relics_config,
    floor_rng,
)


def relic_specs() -> dict[str, dict[str, Any]]:
    """遗物表：{id: 遗物}（始终从 rogue_relics.yaml 读取，勿与塔配置混用）。"""
    return {
        spec["id"]: spec
        for spec in _load_relics_config().get("relics", [])
        if spec.get("id")
    }


def _run_relics(rogue) -> list[str]:
    """本局遗物（run 内生效）；PETROGUE.RELICS 只是图鉴，不提供加成。"""
    return list((rogue.run_state or {}).get("relics") or [])


_RELIC_FLAT_LABELS = {
    "atk": "攻击",
    "def": "防御",
    "spd": "速度",
    "mp": "魔力",
    "max_hp": "生命上限",
}
_RELIC_MULT_LABELS = {
    "atk_mult": "攻击",
    "def_mult": "防御",
    "spd_mult": "速度",
    "mp_mult": "魔力",
    "max_hp_mult": "生命上限",
    "vs_boss_mult": "对首领/精英伤害",
    "coins_mult": "金币",
    "exp_mult": "经验",
}


def _pct(value: float) -> str:
    pct = round(value * 100)
    return f"+{pct}%" if pct >= 0 else f"{pct}%"


def relic_effect_note(effect: dict[str, Any]) -> str:
    """遗物效果的紧凑属性说明（如「攻击+6，金币+10%」），供各展示位附在名称后。

    键与 rogue_relics.yaml 头注释一致：平值键直接加数值；``*_mult`` /
    ``shop_discount`` 按相对 1.0 的百分比；``crit_rate`` 为百分点。
    """
    parts: list[str] = []
    for key, label in _RELIC_FLAT_LABELS.items():
        if key in effect:
            parts.append(f"{label}+{int(effect[key])}")
    if "crit_rate" in effect:
        parts.append(f"暴击率{_pct(float(effect['crit_rate']))}")
    for key, label in _RELIC_MULT_LABELS.items():
        if key in effect:
            parts.append(f"{label}{_pct(float(effect[key]) - 1.0)}")
    if "shop_discount" in effect:
        parts.append(f"商店价格{_pct(float(effect['shop_discount']) - 1.0)}")
    if "start_coins" in effect:
        parts.append(f"开局金币+{int(effect['start_coins'])}")
    if "boss_heal" in effect:
        parts.append(f"击杀首领回复{int(effect['boss_heal'])}生命")
    return "，".join(parts)


def relic_stat_note(relic_id: str) -> str:
    """按遗物 id 查效果表生成属性说明。"""
    spec = relic_specs().get(relic_id) or {}
    return relic_effect_note(spec.get("effect") or {})


def _relic_display(relic_id: str) -> str:
    """图鉴/列表用：``名称（属性说明）``；无效果或查不到时只留名称/id。"""
    spec = relic_specs().get(relic_id)
    if not spec:
        return relic_id
    note = relic_effect_note(spec.get("effect") or {})
    name = spec.get("name", relic_id)
    return f"{name}（{note}）" if note else name


def relic_multipliers(rogue) -> dict[str, float]:
    """收集**本局**遗物的乘区效果（同键叠乘）：atk/def/spd/mp/max_hp/coins/exp/vs_boss 的 *_mult 键与 shop_discount。"""
    specs = relic_specs()
    total: dict[str, float] = {}
    for relic_id in _run_relics(rogue):
        spec = specs.get(relic_id)
        if not spec:
            continue
        for key, value in (spec.get("effect") or {}).items():
            if key.endswith("_mult") or key == "shop_discount":
                total[key] = total.get(key, 1.0) * float(value)
    return total


def relic_stat_bonuses(relic_ids: list[str]) -> dict[str, float]:
    """本局遗物的**平值属性加成**（atk/def/spd/mp/crit_rate），战斗入场时叠加。

    与乘区键（*_mult）互补：平值键直接加在战斗属性上，见 skills/遗物注释。
    """
    specs = relic_specs()
    total: dict[str, float] = dict.fromkeys(
        ("atk", "def", "spd", "mp", "crit_rate"), 0.0
    )
    for relic_id in relic_ids:
        spec = specs.get(relic_id)
        if not spec:
            continue
        for key, value in (spec.get("effect") or {}).items():
            if key in total:
                total[key] += float(value)
    return total


def relic_flat(rogue, key: str) -> float:
    """收集**本局**遗物的固定值效果（start_coins / boss_heal 等）之和。"""
    specs = relic_specs()
    return float(
        sum(
            float((specs[relic_id].get("effect") or {}).get(key, 0))
            for relic_id in _run_relics(rogue)
            if relic_id in specs
        )
    )


def relic_offer(rogue, floor: int, seed: int, count: int = 3) -> list[str]:
    """遗物奖励层的三选一：从**本局未持有**的普通遗物中按 seed 抽 ``count`` 件。"""
    specs = relic_specs()
    owned = set(_run_relics(rogue))
    pool = [
        relic_id
        for relic_id, spec in specs.items()
        if spec.get("rarity") == "normal" and relic_id not in owned
    ]
    if not pool:
        return []
    rng = floor_rng(seed, floor, "relic")
    rng.shuffle(pool)
    return pool[: min(count, len(pool))]


def _grant_relic(rogue, relic_id: str) -> dict[str, Any] | None:
    """获得一件遗物：写入**本局** run.relics（立即生效）并登记到图鉴 RELICS。

    获得时即时生效的效果在这里处理：max_hp 抬局内上限并同步回血、
    start_coins 立即入账（其余效果由战斗/结算实时读取）。调用方负责持久化。
    """
    spec = relic_specs().get(relic_id)
    if spec is None:
        return None
    run = rogue.run_state or {}
    run.setdefault("relics", [])
    if relic_id not in run["relics"]:
        run["relics"].append(relic_id)
    if relic_id not in rogue.relics:
        rogue.relics.append(relic_id)  # 图鉴：见过即记录
    rogue.updated_at = datetime.now()
    effect = spec.get("effect", {}) or {}
    if "max_hp" in effect and run:
        delta = int(effect["max_hp"])
        run["hp_max"] = int(run.get("hp_max", 0)) + delta
        run["hp"] = min(run["hp_max"], int(run.get("hp", 0)) + delta)
    if "start_coins" in effect and run:
        run["coins"] = int(run.get("coins", 0)) + int(effect["start_coins"])
    return spec
