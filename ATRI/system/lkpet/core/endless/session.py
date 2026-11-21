"""无尽模式的战斗会话序列化。

``BattleSession`` 的状态大部分是纯 JSON（units / dots / debuffs / 各类计数），
但有三处不能直接 ``json.dumps``：

1. ``rng`` 是 ``random.Random``，要 ``getstate()`` / ``setstate()``
2. 单位记录里的 ``fired`` 是 ``set``
3. ``actor_cursor`` / ``frame_phase`` 这类三相状态，丢了会与不中断的那次分叉

**rng 必须存**：暴击、闪避、暴击判定三处都从它取值，不存状态就能靠退出重进刷结果。
"""

from __future__ import annotations

from typing import Any

from ..roguelike.battle import BattleSession

SESSION_VERSION = 1
"""序列化格式版本。字段变了要升，读到旧版本直接丢弃重打。"""


def _state_to_json(state: Any) -> list[Any]:
    """``random.Random.getstate()`` 的 tuple → 可 JSON 化的 list。"""
    return [state[0], list(state[1]), state[2]]


def _state_from_json(data: Any) -> Any:
    return (int(data[0]), tuple(int(x) for x in data[1]), data[2])


def _target_index(session: BattleSession) -> int:
    """``current_target`` 在 ``units`` 里的下标；-1 = 没有目标。

    存下标而不是整条记录：记录本身就是 ``units`` 的一项（含 ``fired`` 这个
    set），整条存进去既重复又不可 JSON 化。
    """
    target = session.current_target
    for index, rec in enumerate(session.units):
        if rec is target:
            return index
    return -1


def dump_session(session: BattleSession) -> dict[str, Any]:
    """把战斗会话压成可存 dict。"""
    units = []
    for rec in session.units:
        item = {k: v for k, v in rec.items() if k != "fired"}
        item["fired"] = sorted(rec.get("fired") or [])
        units.append(item)
    return {
        "version": SESSION_VERSION,
        "units": units,
        "dots": session.dots,
        "pet_debuffs": session.pet_debuffs,
        "pet_mods": session.pet_mods,
        "cooldowns": session.cooldowns,
        "gauge_pet": session.gauge_pet,
        "battle_timer": session.battle_timer,
        "frames": session.frames,
        "pet_hp": session.pet_hp,
        "pet_mp": session.pet_mp,
        "mp_cap": session.mp_cap,
        "hp_max": session.hp_max,
        "pet_acts": session.pet_acts,
        "monster_acts": session.monster_acts,
        "crits": session.crits,
        "pet_shield": session.pet_shield,
        "pet_guard_mult": session.pet_guard_mult,
        "pet_shield_gain": session.pet_shield_gain,
        "pet_heal_gain": session.pet_heal_gain,
        "pet_lifesteal": session.pet_lifesteal,
        "pet_debuff_res": session.pet_debuff_res,
        "pet_dodge": session.pet_dodge,
        "accuracy": session.accuracy,
        "pet_element_resist": session.pet_element_resist,
        "pet_combat_mods": session.pet_combat_mods,
        "pet_action_ready": session.pet_action_ready,
        "actor_cursor": session.actor_cursor,
        "pending_actors": session.pending_actors,
        "frame_phase": session.frame_phase,
        "current_target_index": _target_index(session),
        "rng": _state_to_json(session.rng.getstate()),
    }


def load_session(session: BattleSession, data: dict[str, Any]) -> bool:
    """把 dict 还原进已构造好的 session；版本不匹配时返回 False。

    调用方必须先构造 session（构造会重算入场属性与单位），再用本函数覆盖
    可变状态——这样即使序列化缺字段，也能退化成「从入场开始」。
    """
    if not data or int(data.get("version", 0)) != SESSION_VERSION:
        return False
    units = data.get("units") or []
    if not units:
        return False
    if len(units) != len(session.units):
        # 数量对不上多半是**战斗中的召唤物**：它们不在 monster 上，
        # 重建时天然造不出来。按存档把全部单位重建一遍，而不是判失败——
        # 判失败会让带召唤技能的怪每回合都 ``bad_session``。
        session.units = [
            session._spawn_unit(
                dict(saved.get("unit") or {}),
                primary=bool(saved.get("primary")),
                minion=bool(saved.get("minion")),
                pack=bool(saved.get("pack")),
            )
            for saved in units
        ]
    for rec, saved in zip(session.units, units, strict=False):
        saved = dict(saved)
        fired = saved.get("fired") or []
        saved["fired"] = {str(x) for x in fired}
        for key, value in saved.items():
            if key == "unit":
                # 单位面板以构造时的为准，避免存档里的旧面板盖掉新模板
                rec["unit"].update(
                    {
                        k: v
                        for k, v in value.items()
                        if k in ("hp", "mp", "shield", "charge")
                    }
                )
            else:
                rec[key] = value
    session.dots = list(data.get("dots") or [])
    session.pet_debuffs = list(data.get("pet_debuffs") or [])
    session.pet_mods = dict(data.get("pet_mods") or {})
    session.cooldowns = dict(data.get("cooldowns") or {})
    session.gauge_pet = float(data.get("gauge_pet", 0.0))
    session.battle_timer = float(data.get("battle_timer", 0.0))
    session.frames = int(data.get("frames", 0))
    session.pet_hp = int(data.get("pet_hp", session.pet_hp))
    session.pet_mp = int(data.get("pet_mp", session.pet_mp))
    session.mp_cap = int(data.get("mp_cap", session.mp_cap))
    session.hp_max = int(data.get("hp_max", session.hp_max))
    session.pet_acts = int(data.get("pet_acts", 0))
    session.monster_acts = int(data.get("monster_acts", 0))
    session.crits = int(data.get("crits", 0))
    session.pet_shield = int(data.get("pet_shield", 0))
    session.pet_guard_mult = float(data.get("pet_guard_mult", 1.0) or 1.0)
    session.pet_shield_gain = float(data.get("pet_shield_gain", 0.0) or 0.0)
    session.pet_heal_gain = float(data.get("pet_heal_gain", 0.0) or 0.0)
    session.pet_lifesteal = float(data.get("pet_lifesteal", 0.0) or 0.0)
    session.pet_debuff_res = float(data.get("pet_debuff_res", 0.0) or 0.0)
    session.pet_dodge = float(data.get("pet_dodge", 0.0) or 0.0)
    session.accuracy = float(data.get("accuracy", 0.0) or 0.0)
    session.pet_element_resist = dict(data.get("pet_element_resist") or {})
    session.pet_combat_mods = dict(data.get("pet_combat_mods") or {})
    session.pet_action_ready = bool(data.get("pet_action_ready", False))
    session.actor_cursor = int(data.get("actor_cursor", 0))
    session.pending_actors = [tuple(x) for x in (data.get("pending_actors") or [])]
    session.frame_phase = str(data.get("frame_phase") or "idle")
    index = data.get("current_target_index")
    units_list = session.units
    if isinstance(index, int) and 0 <= index < len(units_list):
        session.current_target = units_list[index]
    else:
        session.current_target = None
    rng_state = data.get("rng")
    if rng_state:
        session.rng.setstate(_state_from_json(rng_state))
    return True
