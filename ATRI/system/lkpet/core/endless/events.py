"""无尽模式的事件：抽取、结算、渲染。

数据沿用 ``rogue_events.yaml``（按区域分组）——结构相同，内容可以直接复用。
每区域 31 个事件，kind 有 9 类。

effect 支持 6 个键（数据实测得到，不是猜的）：

    run_hp       局内生命（下限 1，不致死）
    run_mp       局内魔力
    coins        局内金币
    exp          局内经验
    roll         [{weight, text, effect}] 按权重抽一个分支
    elite_fight  触发一场强化战斗

另外额外支持 ``relic`` / ``relic_tier`` / ``curse`` / ``chest``，现有数据没用上，留了接口。
"""

from __future__ import annotations

import random
from typing import Any

KINDS = (
    "blessing",
    "trade",
    "disaster",
    "gamble",
    "trap",
    "peril",
    "boon",
    "flavor",
    "elite_fight",
)

KIND_CN = {
    "blessing": "福缘",
    "trade": "抉择",
    "disaster": "逆境",
    "gamble": "赌局",
    "trap": "诱饵",
    "peril": "险境",
    "boon": "机遇",
    "flavor": "见闻",
    "elite_fight": "强化战斗",
}


def load_events_config() -> dict[str, Any]:
    from ...config_loader import load_config

    return load_config("rogue_events")


def region_events(
    region: str, cfg: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    cfg = cfg or load_events_config()
    raw = (cfg.get("events") or {}).get(str(region)) or []
    return [dict(e) for e in raw]


def pick_event(
    run: Any,
    rng: random.Random,
    region: str,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """抽一个本区域**还没触发过**的事件。

    一次 run 内每个事件只触发一次；全触发过就返回 None，
    调用方退回「这一层没有事件、直接前进」。
    """
    seen = {str(x) for x in (getattr(run, "seen_events", None) or [])}
    pool = [e for e in region_events(region, cfg) if str(e.get("id")) not in seen]
    if not pool:
        return None
    return dict(rng.choice(pool))


def resolve_roll(
    roll: list[dict[str, Any]], rng: random.Random
) -> dict[str, Any] | None:
    """按权重抽一个分支；权重 <= 0 的项不参与。"""
    items = [b for b in (roll or []) if float(b.get("weight", 0) or 0) > 0]
    if not items:
        return None
    total = sum(float(b.get("weight", 0) or 0) for b in items)
    point = rng.uniform(0, total)
    acc = 0.0
    for branch in items:
        acc += float(branch.get("weight", 0) or 0)
        if point <= acc:
            return dict(branch)
    return dict(items[-1])


def apply_effect(
    run: Any,
    effect: dict[str, Any],
    *,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """把一个 effect 结算到 run 上；返回实际变化量（供回执）。

    ``run_hp`` 有下限保护：事件伤害最多把生命压到 1，不致死——
    否则一次手气差的事件就能把人送走，那是「被随机数玩死」不是「冒险」。
    """
    rng = rng or random.Random(0)
    changed: dict[str, Any] = {"hp": 0, "mp": 0, "coins": 0, "exp": 0}

    if "roll" in effect:
        branch = resolve_roll(list(effect.get("roll") or []), rng)
        if branch:
            inner = apply_effect(run, branch.get("effect") or {}, rng=rng)
            inner["branch"] = str(branch.get("text") or "")
            return inner

    hp = int(effect.get("run_hp", 0) or 0)
    if hp > 0:
        before = run.hp
        run.hp = min(run.hp_max, run.hp + hp)
        changed["hp"] = run.hp - before
    elif hp < 0:
        before = run.hp
        run.hp = max(1, run.hp + hp)
        changed["hp"] = run.hp - before

    mp = int(effect.get("run_mp", 0) or 0)
    if mp:
        before = run.mp
        run.mp = max(0, min(run.mp_max, run.mp + mp))
        changed["mp"] = run.mp - before

    coins = int(effect.get("coins", 0) or 0)
    if coins:
        run.coins = max(0, run.coins + coins)
        changed["coins"] = coins

    exp = int(effect.get("exp", 0) or 0)
    if exp:
        from .progression import award_exp

        levels = award_exp(run.progression, exp)
        changed["exp"] = exp
        changed["levels"] = levels

    if "elite_fight" in effect:
        changed["elite_fight"] = True

    if "relic" in effect or "relic_tier" in effect:
        changed["relic"] = effect.get("relic_tier") or effect.get("relic")

    if "curse" in effect:
        changed["curse"] = True

    if "chest" in effect:
        changed["chest"] = True

    return changed


def choose_event(
    run: Any,
    event: dict[str, Any],
    index: int,
    *,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """选第 index 个选项（从 1 起）并结算。"""
    choices = list(event.get("choices") or [])
    if not 1 <= int(index) <= len(choices):
        return {"ok": False, "reason": "out_of_range"}
    choice = choices[int(index) - 1]
    changed = apply_effect(run, choice.get("effect") or {}, rng=rng)
    event_id = str(event.get("id") or "")
    if event_id and event_id not in run.seen_events:
        run.seen_events.append(event_id)
    changed["ok"] = True
    changed["text"] = str(choice.get("text") or "")
    return changed


def render_event(event: dict[str, Any]) -> str:
    """把事件渲染成描述 + 编号选项（QQ 端整行可点）。

    **不写分类标签**：``kind`` 只作内部配比统计用，写出来等于剧透这个事件
    是福缘还是陷阱——玩家该从文案本身判断。
    """
    from ..roguelike._common import line_link, show_numbered_options

    numbered = show_numbered_options()
    rows: list[str] = ["🎲 " + str(event.get("text") or "")]
    for index, choice in enumerate(event.get("choices") or [], 1):
        label = str(choice.get("text") or "")
        body = f"{index}. {label}" if numbered else label
        rows.append(line_link(body, f"宠物.无尽 {index}"))
    return "\n".join(rows)


def render_result(changed: dict[str, Any]) -> str:
    """结算回执：只报变了什么，没变的不写。"""
    parts = []
    if changed.get("branch"):
        parts.append("（" + str(changed["branch"]) + "）")
    if changed.get("hp"):
        parts.append("生命 " + format(int(changed["hp"]), "+d"))
    if changed.get("mp"):
        parts.append("魔力 " + format(int(changed["mp"]), "+d"))
    if changed.get("coins"):
        parts.append("金币 " + format(int(changed["coins"]), "+d"))
    if changed.get("exp"):
        parts.append("经验 +" + str(changed["exp"]))
    return "\u3000".join(parts) or "什么都没发生。"
