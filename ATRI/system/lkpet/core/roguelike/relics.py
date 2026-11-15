"""遗物系统：图鉴、效果、乘区、掉落、授予与定价。"""

from datetime import datetime
from typing import Any

from ._common import (
    _load_relics_config,
    floor_rng,
)

#: 平值属性每点折算成「战力 %」的权重（atk=1 为基准，其余按战斗公式比例）。
_FLAT_WEIGHT = {"atk": 1.0, "def": 0.41, "spd": 0.55, "max_hp": 0.36, "mp": 0.09}

#: 百分比效果每 1% 折算成的「战力 %」。
_PCT_WEIGHT = {
    "atk_mult": 1.00,
    "def_mult": 1.00,
    "spd_mult": 1.00,
    "max_hp_mult": 1.00,
    "mp_mult": 0.15,
    "vs_boss_mult": 0.35,
    "coins_mult": 0.40,
    "exp_mult": 0.30,
}

#: 数值越小越好的键：按 ``1 - value`` 折算。
_DISCOUNT_WEIGHT = {"shop_discount": 0.50}

#: 直接按数值折算的键；``crit_rate`` 存的是小数（0.12 = 12%），要乘 100。
_ABS_WEIGHT = {"crit_rate": 0.80, "boss_heal": 0.05}

#: 面板取 30 级 + 约 1.3 倍综合加成（年龄/天赋/轮回/装备）。
_PRICE_REF_LEVEL = 30.0
_PANEL_ENV = 1.3


def _panel(level: float) -> dict[str, float]:
    """等级 ``level`` 的宠物面板（atk/def/spd/hp），用于折算遗物价值。"""
    from ...config_loader import load_config

    pet = load_config("pet")
    base, growth = pet["base_attributes"], pet["growth"]
    return {
        "atk": (base["atk"] + growth["atk"] * level) * _PANEL_ENV,
        "def": (base["def"] + growth["def"] * level) * _PANEL_ENV,
        "spd": (base["spd"] + growth["spd"] * level) * _PANEL_ENV,
        "hp": (base["hp"] + growth["hp"] * level) * _PANEL_ENV,
    }


def _per_point(
    level: float, rogue_cfg: dict[str, Any] | None = None
) -> dict[str, float]:
    """``level`` 级时每点属性值多少「战力 %」：面板越高越低。"""
    panel = _panel(level)
    softness, spd_exp = 100.0, 0.5
    if rogue_cfg is not None:
        battle = rogue_cfg.get("battle") or {}
        softness = float(battle.get("def_softness", softness))
        spd_exp = float(battle.get("spd_exponent", spd_exp))
    atk = panel["atk"]
    return {
        "atk": 100.0 / atk,
        "def": 100.0 / (softness + panel["def"]),
        "spd": 100.0 * spd_exp / panel["spd"],
        "max_hp": 100.0 / panel["hp"],
        "mp": _FLAT_WEIGHT["mp"] * 100.0 / atk,
    }


def relic_flat_power(
    effect: dict[str, Any],
    level: float = _PRICE_REF_LEVEL,
    rogue_cfg: dict[str, Any] | None = None,
) -> float:
    """``effect`` 里平值部分的战力收益（%）——随等级衰减的就是这一段。"""
    pp = _per_point(level, rogue_cfg)
    return sum(float(effect[key]) * pp[key] for key in pp if key in effect)


def relic_pct_power(effect: dict[str, Any]) -> float:
    """``effect`` 里百分比部分的战力收益（%）——与等级无关。"""
    value = 0.0
    if "crit_rate" in effect:
        value += float(effect["crit_rate"]) * 100 * _ABS_WEIGHT["crit_rate"]
    if "boss_heal" in effect:
        value += float(effect["boss_heal"]) * _ABS_WEIGHT["boss_heal"]
    for key, weight in _PCT_WEIGHT.items():
        if key in effect:
            value += (float(effect[key]) - 1.0) * 100 * weight
    for key, weight in _DISCOUNT_WEIGHT.items():
        if key in effect:
            value += (1.0 - float(effect[key])) * 100 * weight
    return value


def relic_power(
    effect: dict[str, Any],
    level: float = _PRICE_REF_LEVEL,
    rogue_cfg: dict[str, Any] | None = None,
) -> float:
    """``effect`` 在 ``level`` 级时的总战力收益（%）。"""
    return relic_flat_power(effect, level, rogue_cfg) + relic_pct_power(effect)


def relic_floor_decay(
    effect: dict[str, Any], floor: int, rogue_cfg: dict[str, Any] | None = None
) -> float:
    """平值段在 ``floor`` 层相对 30 级的衰减倍率（0.3~2.0）。"""
    now = relic_flat_power(effect, float(floor), rogue_cfg)
    ref = relic_flat_power(effect, _PRICE_REF_LEVEL, rogue_cfg)
    if ref <= 0:
        return 1.0
    return min(2.0, max(0.3, now / ref))


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
    """遗物效果的紧凑属性说明（如「攻击+6（+3%），金币+10%」）。"""
    parts: list[str] = []
    for key, label in _RELIC_FLAT_LABELS.items():
        if key not in effect:
            continue
        mult = effect.get(f"{key}_mult")
        text = f"{label}+{int(effect[key])}"
        if isinstance(mult, int | float) and mult != 1.0:
            text += f"（{_pct(float(mult) - 1.0)}）"
        parts.append(text)
    if "crit_rate" in effect:
        parts.append(f"暴击率{_pct(float(effect['crit_rate']))}")
    for key, label in _RELIC_MULT_LABELS.items():
        if key in effect and key[:-5] not in _RELIC_FLAT_LABELS:
            parts.append(f"{label}{_pct(float(effect[key]) - 1.0)}")
    if "shop_discount" in effect:
        parts.append(f"商店价格{_pct(float(effect['shop_discount']) - 1.0)}")
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
    return relic_multipliers_from_ids(_run_relics(rogue))


def relic_multipliers_from_ids(relic_ids: list[str]) -> dict[str, float]:
    """按遗物 id 列表收集乘区效果（同键乘算）。"""
    specs = relic_specs()
    total: dict[str, float] = {}
    for relic_id in relic_ids:
        spec = specs.get(relic_id)
        if not spec:
            continue
        for key, value in (spec.get("effect") or {}).items():
            if key.endswith("_mult") or key == "shop_discount":
                total[key] = total.get(key, 1.0) * float(value)
    return total


def relic_stat_bonuses(relic_ids: list[str]) -> dict[str, float]:
    """本局遗物的平值属性加成（atk/def/spd/mp/crit_rate），战斗入场时叠加。"""
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
    """收集**本局**遗物的固定值效果（boss_heal 等）之和。"""
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


def elite_fight_relic(rogue, floor: int, seed: int, spec: str = "auto") -> str | None:
    """强化精英胜利后掉落的遗物 id；没有可掉落的返回 ``None``。

    ``spec`` 为具体 relic id 时直接用它（若本局已持有则放弃）；
    为 ``auto`` 时从「本局未持有的普通遗物」里按 ``(seed, floor, "elite_fight_relic")``
    抽一件——与遗物奖励层**共用同一个池**，但随机源独立，
    避免同层的三选一与精英掉落总是给出同一件。
    """
    owned = set(_run_relics(rogue))
    if spec and spec != "auto":
        return None if spec in owned else spec

    pool = sorted(
        relic_id
        for relic_id, item in relic_specs().items()
        if item.get("rarity") == "normal" and relic_id not in owned
    )
    if not pool:
        return None
    rng = floor_rng(seed, floor, "elite_fight_relic")
    return pool[rng.randrange(len(pool))]


def _grant_relic(rogue, relic_id: str) -> dict[str, Any] | None:
    """获得一件遗物：写入**本局** run.relics（立即生效）并登记到图鉴 RELICS。

    生命上限的平值与乘区都在这里结算（乘区作用于当前上限），调用方负责持久化。
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
    if run and ("max_hp" in effect or "max_hp_mult" in effect):
        before = int(run.get("hp_max", 0))
        after = round(before * float(effect.get("max_hp_mult", 1.0)))
        after += int(effect.get("max_hp", 0))
        delta = after - before
        run["hp_max"] = after
        run["hp"] = min(after, int(run.get("hp", 0)) + max(0, delta))
    return spec
