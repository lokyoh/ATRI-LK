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


RELIC_POOL_CFG = "endless_event_pool"
"""会发遗物的专属事件（与爬塔共用的基础事件表分开维护）。"""


def relic_pool(region: str) -> list[dict[str, Any]]:
    """本区域发遗物的专属事件；文件缺失时返回空表。"""
    from ...config_loader import load_config

    try:
        node = load_config(RELIC_POOL_CFG)
    except FileNotFoundError:
        return []
    raw = (node.get("events") or {}).get(str(region)) or []
    return [dict(e) for e in raw]


def region_events(
    region: str, cfg: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """基础事件 + 发遗物的专属事件，合成一个池。"""
    cfg = cfg or load_events_config()
    raw = (cfg.get("events") or {}).get(str(region)) or []
    return [dict(e) for e in raw] + relic_pool(region)


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
    # 会发遗物的事件一律补一个「不拿」选项：遗物必须是可选的
    return with_skip_choice(dict(rng.choice(pool)))


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

    if "relic" in effect or "relic_tier" in effect or "event_relic" in effect:
        spec = _grant_event_relic(run, effect, rng)
        if spec is not None:
            changed["relic"] = str(spec.get("id"))
            changed["relic_name"] = str(spec.get("name") or "")

    if "curse" in effect:
        spec = _grant_event_relic(run, effect, rng, cursed_only=True)
        if spec is not None:
            changed["curse"] = str(spec.get("id"))
            changed["curse_name"] = str(spec.get("name") or "")

    if "skip_relic" in effect:
        from .flow import grant_reward

        paid = grant_reward(run, _skip_ratio())
        changed["exp"] = int(paid.get("exp", 0) or 0)
        changed["coins"] = int(paid.get("coins", 0) or 0)
        changed["levels"] = int(paid.get("levels", 0) or 0)

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
    changed["skipped"] = "skip_relic" in (choice.get("effect") or {})
    return changed


def _grant_event_relic(
    run: Any,
    effect: dict[str, Any],
    rng: random.Random,
    *,
    cursed_only: bool = False,
) -> dict[str, Any] | None:
    """把一件事件遗物发给 run；点名 id 优先，否则按权重随机。"""
    from .relics import event_relic_specs, roll_event_relic

    owned = {str(r): 1 for r in (getattr(run, "relics", None) or [])}
    named = str(effect.get("event_relic") or effect.get("relic") or "")
    if named:
        spec = event_relic_specs().get(named)
        if spec is not None:
            run.relics.append(named)
            return spec
    spec = roll_event_relic(rng, owned, cursed_only=cursed_only)
    if spec is None:
        return None
    run.relics.append(str(spec["id"]))
    return spec


def _skip_ratio() -> float:
    """不拿遗物时补偿的战斗奖励比例（endless.yaml 的 events.relic_skip_ratio）。"""
    from .config import load_endless_config

    node = load_endless_config().get("events") or {}
    return float(node.get("relic_skip_ratio", 0.8) or 0.8)


def _skip_label() -> str:
    """「不拿遗物」那个选项的默认文案。"""
    from .config import load_endless_config

    node = load_endless_config().get("events") or {}
    return str(node.get("relic_skip_text") or "不碰它，只收下盘缠")


def with_skip_choice(
    event: dict[str, Any],
    *,
    ratio: float | None = None,
) -> dict[str, Any]:
    """给「会发遗物」的事件补一个「不拿」选项。

    遗物必须是可选的：不选它就拿一份略低于普通战斗的金币与经验。
    已有 skip_relic 的事件不重复追加。
    """
    choices = list(event.get("choices") or [])
    if not choices:
        return event
    if any(_grants_relic(c) for c in choices) is False:
        return event
    if any("skip_relic" in (c.get("effect") or {}) for c in choices):
        return event
    payload: dict[str, Any] = {"skip_relic": True}
    if ratio is not None:
        payload["ratio"] = float(ratio)
    out = dict(event)
    out["choices"] = [
        *choices,
        {"text": _skip_label(), "effect": payload},
    ]
    return out


def _grants_relic(choice: dict[str, Any]) -> bool:
    """这个选项会不会给遗物（含 curse 与 roll 分支）。"""
    effect = choice.get("effect") or {}
    if any(k in effect for k in ("relic", "relic_tier", "event_relic", "curse")):
        return True
    for branch in effect.get("roll") or []:
        if _grants_relic({"effect": branch.get("effect") or {}}):
            return True
    return False


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
        parts.append("下一场魔力 " + format(int(changed["mp"]), "+d"))
    if changed.get("coins"):
        parts.append("金币 " + format(int(changed["coins"]), "+d"))
    if changed.get("exp"):
        parts.append("经验 +" + str(changed["exp"]))
    if changed.get("relic_name"):
        parts.append("获得遗物【" + str(changed["relic_name"]) + "】")
    if changed.get("curse_name"):
        parts.append("获得【" + str(changed["curse_name"]) + "】")
    if changed.get("skipped"):
        parts.append("放弃遗物，换一份盘缠")
    return "\u3000".join(parts) or "什么都没发生。"
