"""无尽模式的属性装配。

引擎（battle.py）只认识乘区字典和几个标量字段，属性名与它们的映射全在这里。

空字典 = 爬塔路径：不调用本模块时引擎行为与改动前逐点一致。
"""

from __future__ import annotations

from typing import Any

from ..roguelike._common import PASSIVE_STAT_KEYS
from .combat import build_pet_mods, resistance_scale

DEFAULT_ATTRS: dict[str, float] = {
    "atk": 0.0,
    "def": 0.0,
    "spd": 0.0,
    "hp": 0.0,
    "mp": 0.0,
    "atk_pct": 0.0,
    "def_pct": 0.0,
    "hp_pct": 0.0,
    "mp_pct": 0.0,
    "crit_rate": 0.0,
    "crit_mult": 0.0,
    "armor_pen": 0.0,
    "solid": 0.0,
    "basic_dmg": 0.0,
    "skill_dmg": 0.0,
    "phys_dmg": 0.0,
    "phys_res": 0.0,
    "elem_fire": 0.0,
    "elem_ice": 0.0,
    "elem_thunder": 0.0,
    "elem_wind": 0.0,
    "elem_earth": 0.0,
    "elem_light": 0.0,
    "elem_dark": 0.0,
    "elem_all": 0.0,
    "lifesteal": 0.0,
    "dodge": 0.0,
    "accuracy": 0.0,
    "shield_gain": 0.0,
    "heal_gain": 0.0,
    "debuff_res": 0.0,
}
"""玩家侧属性的零基准。crit_mult 是**加成量**，基准 1.8 在 CRIT_BASE。

键名与 :mod:``endless_relics.yaml`` / ``endless_gear.yaml`` 的数据一致——
数据里写什么键，这里就要有同名键，否则那些效果会被静默丢掉。
``element_resist`` 是 dict，单独按元素累加（见 :func:``merge_attrs``）。
"""

CRIT_BASE = 1.8
"""暴击倍率基准值（原 battle.py:183 的硬编码）。"""


def merge_attrs(*layers: dict[str, Any] | None) -> dict[str, float]:
    """把多份属性平值相加。

    * 标量：按同名键累加；白名单外的键**原样保留**（未来加属性不用改这里）
    * dict 值（``element_resist``）：按内层键累加
    """
    merged = dict(DEFAULT_ATTRS)
    for layer in layers:
        if not layer:
            continue
        for key, value in layer.items():
            if isinstance(value, dict):
                node = merged.get(key)
                if not isinstance(node, dict):
                    node = {}
                    merged[key] = node
                for sub, amount in value.items():
                    if isinstance(amount, int | float):
                        node[str(sub)] = float(node.get(str(sub), 0.0) or 0.0) + float(
                            amount
                        )
                continue
            if isinstance(value, int | float):
                merged[key] = float(merged.get(key, 0.0) or 0.0) + float(value)
    return merged


def crit_multiplier(attrs: dict[str, float]) -> float:
    """最终暴击倍率 = 基准 + 加成。"""
    return CRIT_BASE + float(attrs.get("crit_mult", 0.0) or 0.0)


def apply_to_session(session: Any, attrs: dict[str, float]) -> None:
    """把属性挂到 BattleSession 上（改字段，不改算法）。"""
    session.crit_rate = float(attrs.get("crit_rate", 0.0) or 0.0)
    session.crit_mult = crit_multiplier(attrs)
    session.pet_dodge = float(attrs.get("dodge", 0.0) or 0.0)
    session.accuracy = float(attrs.get("accuracy", 0.0) or 0.0)
    session.pet_lifesteal = float(attrs.get("lifesteal", 0.0) or 0.0)
    session.pet_heal_gain = float(attrs.get("heal_gain", 0.0) or 0.0)
    session.pet_shield_gain = float(attrs.get("shield_gain", 0.0) or 0.0)
    session.pet_debuff_res = float(attrs.get("debuff_res", 0.0) or 0.0)
    session.pet_solid = float(attrs.get("solid", 0.0) or 0.0)
    resist = attrs.get("element_resist") or {}
    if isinstance(resist, dict):
        session.pet_element_resist = {
            str(k): resistance_scale(str(k), {str(k): v}) for k, v in resist.items()
        }
    else:
        session.pet_element_resist = {}


def make_pet_mods(
    attrs: dict[str, float],
    *,
    element: str = "normal",
    enemy_element: str = "normal",
    kind: str = "attack",
) -> dict[str, float]:
    """构造本回合的乘区字典（外层每次出手前刷新）。"""
    return build_pet_mods(
        element=element,
        enemy_element=enemy_element,
        kind=kind,
        attrs=attrs,
    )


PASSIVE_KEYS = PASSIVE_STAT_KEYS
"""被动技能的加成键（与爬塔同口径）。"""
