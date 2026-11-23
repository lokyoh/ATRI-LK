"""无尽模式的局内流程：选路线、进战斗、升级选属性、商店。

把 ``core/endless`` 各模块串成一条推进链：

    层 pending(floor)  →  选路线  →  战斗 / 事件 / 商店
    战斗结束          →  结算经验  →  有升级就弹 4 选 1
    层 9 进商店        →  买 / 刷新 / 离开

这一层不做规则判定，只负责「把玩家的编号翻译成动作」并推进状态。
"""

from __future__ import annotations

import random
from math import floor
from typing import Any

from .config import load_endless_config, template_attributes
from .floor import floor_kind, is_boss_floor, is_elite_floor, stage_of
from .gear import equipped_effects
from .progression import (
    LEVEL_KEYS,
    Progression,
    apply_choice,
    award_exp,
    combat_attributes,
)
from .relics import set_bonuses, total_stat_bonuses
from .run import RogueRun

REWARD_EXP = {1: 40, 2: 40}
"""普通 / 强化的经验基准（来自 endless.yaml 的 rewards.exp）。"""


def _cfg() -> dict[str, Any]:
    return load_endless_config()


def exp_reward(
    run: RogueRun,
    *,
    elite: bool = False,
    boss: bool = False,
    elite_fight: bool = False,
    stage: int | None = None,
) -> int:
    """本层战斗的经验奖励。"""
    cfg = _cfg()
    table = (cfg.get("rewards") or {}).get("exp") or {}
    st = stage if stage is not None else stage_of(run.floor)
    base_scale = 1.0 + 0.15 * (st - 1)
    if boss:
        key = "boss"
    elif elite:
        key = "elite"
    elif elite_fight:
        key = "elite_fight"
    else:
        key = "normal"
    return max(1, round(int(table.get(key, 40) or 40) * base_scale))


def coin_reward(run: RogueRun, *, elite: bool = False, boss: bool = False) -> int:
    """金币奖励：固定基准 ±30% 随机，不随层数上涨。"""
    cfg = _cfg()
    table = (cfg.get("rewards") or {}).get("coins") or {}
    base = int(table.get("boss" if boss else "elite" if elite else "normal", 40) or 40)
    spread = float((cfg.get("rewards") or {}).get("variance", 0.30))
    lo = 1.0 - spread
    hi = 1.0 + spread
    rng = random.Random(f"{run.seed}:{run.floor}:coins")
    return max(1, round(base * rng.uniform(lo, hi)))


def combat_attrs(run: RogueRun) -> dict[str, float]:
    """模板面板 + 升级加成 + 遗物平值/套装 + 装备，合成战斗属性表。"""
    cfg = _cfg()
    base = combat_attributes(template_attributes(cfg), run.progression)
    relic_owned = {str(r): 1 for r in run.relics}
    flats = total_stat_bonuses(relic_owned)
    sets = set_bonuses(relic_owned)
    gear = equipped_effects(
        run.equip, None, (run.pending or {}).get("gear_enhance") or {}
    )
    out: dict[str, float] = {str(k): float(v) for k, v in base.items()}
    # 模板 def=0：靠 balance.base_def 给一个下限，否则「升防御」前期毫无意义
    out["def"] = out.get("def", 0.0) + float(
        (cfg.get("balance") or {}).get("base_def", 0) or 0
    )
    for source in (flats, gear):
        for key, value in source.items():
            out[key] = out.get(key, 0.0) + float(value)
    for key, value in sets.items():
        out[key] = out.get(key, 0.0) + float(value)
    return out


def stage_recovery(run: RogueRun, cfg: dict[str, Any] | None = None) -> dict[str, int]:
    """阶段结束时的回复：满上限的 30%。

    **每层不回血**（早期实现那个 floor_recovery 已删除）：周内靠升级撑血，
    阶段交界才回一次。比例读 ``balance.stage_recovery``。
    """
    cfg = cfg or _cfg()
    node = (cfg.get("balance") or {}).get("stage_recovery") or {}
    pct = float(node.get("hp_pct", 0.30))
    hp_gain = min(run.hp_max - run.hp, max(0, floor(run.hp_max * pct)))
    run.hp += hp_gain
    return {"hp": hp_gain}


def clear_floor(
    run: RogueRun,
    *,
    victor: bool = True,
    elite: bool = False,
    boss: bool = False,
    elite_fight: bool = False,
) -> dict[str, Any]:
    """走完一层：结算奖励 → 掉落。（不回血，阶段回血在 advance 时做）

    所有「打完一层该发生的事」都收在这里，handler 只调这一个函数；
    分散到 handler 里迟早会出现「这条路径忘了给奖励」。
    """
    from .encounter import drop_rewards

    reward = settle_battle(
        run, victor=victor, elite=elite, boss=boss, elite_fight=elite_fight
    )
    drops = (
        drop_rewards(run, elite=elite, boss=boss, elite_fight=elite_fight)
        if victor
        else {}
    )
    reward["drops"] = drops
    return reward


def settle_battle(
    run: RogueRun,
    *,
    victor: bool,
    elite: bool = False,
    boss: bool = False,
    elite_fight: bool = False,
) -> dict[str, Any]:
    """战斗结束结算：给经验与金币，返回「升了几级、要不要弹 4 选 1」。

    输了也能拿一部分经验——否则一次失手就白打，节奏太硬。
    """
    got_exp = exp_reward(run, elite=elite, boss=boss, elite_fight=elite_fight)
    got_coins = coin_reward(run, elite=elite, boss=boss)
    if not victor:
        got_coins = 0
    levels = award_exp(run.progression, got_exp)
    run.coins += got_coins
    return {
        "exp": got_exp,
        "coins": got_coins,
        "levels": levels,
        "need_choice": levels > 0,
    }


def resource_caps(run: RogueRun) -> tuple[int, int]:
    """生命/魔力上限 = 模板 + 升级 + 遗物 + 套装 + 装备。

    不能只算 ``combat_attributes``：那样 hp/mp 平值只在面板里存在，
    战斗和进度条都不认（审查 P1-6）。战斗的 ``state["hp_max"]`` 取的就是这里。
    """
    attrs = combat_attrs(run)
    return max(1, int(attrs.get("hp", 40) or 40)), max(
        0, int(attrs.get("mp", 10) or 10)
    )


def sync_resources(run: RogueRun) -> None:
    """重算上限：涨了就把差额补给当前值，掉了就压回去。"""
    hp_cap, mp_cap = resource_caps(run)
    if hp_cap > run.hp_max:
        run.hp += hp_cap - run.hp_max
    run.hp_max = hp_cap
    run.hp = min(run.hp, run.hp_max)
    run.mp_max = mp_cap
    run.mp = min(run.mp, run.mp_max)


def choose_level(run: RogueRun, key: str) -> bool:
    """应用一次升级 4 选 1，然后同步资源上限。"""
    cfg = _cfg()
    ok = apply_choice(run.progression, key, cfg)
    if ok:
        sync_resources(run)
    return ok


def pending_levels(run: RogueRun) -> int:
    return len(run.progression.pending)


def current_stage(run: RogueRun) -> int:
    return stage_of(run.floor)


def floor_meta(run: RogueRun) -> dict[str, Any]:
    return {
        "floor": run.floor,
        "stage": stage_of(run.floor),
        "kind": floor_kind(run.floor),
        "elite": is_elite_floor(run.floor),
        "boss": is_boss_floor(run.floor),
    }


def learn_and_equip(run: RogueRun, skill_id: str) -> bool:
    """学会一个技能；**只在有空槽时自动装上**，槽满不动已装的。

    槽满时只进已学列表（面板里「换上」才装）。换掉哪一个属于构筑决定，
    系统替玩家换等于把他的选择拿走了。
    """
    learn_skill(run, skill_id)
    active = [str(s) for s in (run.skills.get("active") or [])]
    from .skills import active_slots

    if str(skill_id) in active or len(active) >= active_slots():
        return False
    active.append(str(skill_id))
    run.skills = {**run.skills, "active": active, "passive": []}
    return True


def known_skills(run: RogueRun) -> list[str]:
    """已学会的技能（含换下的）：设计 §8.1「换下的进已学列表，换回来免费」。

    没写过 ``known`` 的老存档按已装的那几个算，不至于读成空。
    """
    known = [str(s) for s in (run.skills.get("known") or [])]
    if known:
        return known
    return [str(s) for s in (run.skills.get("active") or [])]


def learn_skill(run: RogueRun, skill_id: str) -> list[str]:
    """把一个技能记进已学列表。

    必须**总是写回** ``known``：:func:`known_skills` 在缺 ``known`` 时退回
    ``active``，照那个返回值判「已学过」会让首次写入变成空操作。
    """
    known = [str(s) for s in (run.skills.get("known") or [])]
    if not known:
        known = [str(s) for s in (run.skills.get("active") or [])]
    if str(skill_id) not in known:
        known.append(str(skill_id))
    run.skills = {**run.skills, "known": known}
    return known


def skill_slots(run: RogueRun) -> list[str]:
    return [str(s) for s in (run.skills.get("active") or [])]


def equipped_gear_stats(run: RogueRun) -> dict[str, float]:
    return equipped_effects(
        run.equip, None, (run.pending or {}).get("gear_enhance") or {}
    )


def available_level_keys() -> tuple[str, ...]:
    return LEVEL_KEYS


def progression_state(run: RogueRun) -> dict[str, Any]:
    prog: Progression = run.progression
    return {
        "level": prog.level,
        "exp": prog.exp,
        "exp_next": prog.exp_next,
        "bonuses": dict(prog.bonuses),
        "pending": len(prog.pending),
    }
