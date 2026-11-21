"""无尽模式的商店会话：进店、买、刷新、离开。

``shop.roll_shop`` 只负责「生成一排货架」，这里负责**一次进店的完整会话**：
货架存在 ``run.pending['shop']``，刷新次数跟着这一局走，离开就丢弃。

买走的位置变空、不补货；刷新重刷全部位置并累加次数。
"""

from __future__ import annotations

import random
from typing import Any

from .floor import stage_of
from .shop import buy, refresh_cost, roll_shop


def open_shop(
    run: Any,
    *,
    rng: random.Random | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """进店：生成货架并记进 pending。

    已经有货架就直接复用——**重复进同一家店不该重新洗牌**，
    否则玩家可以靠「退出再进」免费刷新。
    """
    existing = (run.pending or {}).get("shop")
    if isinstance(existing, dict) and existing.get("shelf"):
        return existing
    rng = rng or random.Random(f"{run.seed}:{run.floor}:shop")
    shelf = roll_shop(
        rng,
        stage_of(int(getattr(run, "floor", 1) or 1)),
        owned_relics={str(r): 1 for r in (getattr(run, "relics", None) or [])},
        known_skills=list(_active_skills(run)),
        cfg=cfg,
    )
    run.pending.pop("type", None)
    run.pending.pop("options", None)
    session = {"shelf": shelf, "refreshes": 0}
    run.pending["shop"] = session
    return session


def _active_skills(run: Any) -> list[str]:
    skills = getattr(run, "skills", None) or {}
    if isinstance(skills, dict):
        return [str(s) for ids in skills.values() for s in (ids or [])]
    return [str(s) for s in skills]


def shop_state(run: Any) -> dict[str, Any] | None:
    node = (getattr(run, "pending", None) or {}).get("shop")
    return node if isinstance(node, dict) else None


def refresh_shop(
    run: Any,
    *,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """刷新货架；返回 ``{ok, cost, coins, refreshes}``。

    **没有次数上限**：只要金币够就能一直刷，价格线性递增。
    """
    session = shop_state(run)
    if session is None:
        return {"ok": False, "reason": "no_shop"}
    times = int(session.get("refreshes", 0) or 0)
    cost = refresh_cost(times)
    if run.coins < cost:
        return {"ok": False, "reason": "poor", "cost": cost}
    run.coins -= cost
    rng = random.Random(f"{run.seed}:{run.floor}:shop:{times + 1}")
    shelf = roll_shop(
        rng,
        stage_of(int(getattr(run, "floor", 1) or 1)),
        owned_relics={str(r): 1 for r in (getattr(run, "relics", None) or [])},
        known_skills=list(_active_skills(run)),
        cfg=cfg,
    )
    session["shelf"] = shelf
    session["refreshes"] = times + 1
    return {"ok": True, "cost": cost, "coins": run.coins, "refreshes": times + 1}


def purchase(
    run: Any,
    index: int,
) -> dict[str, Any]:
    """买第 index 个位置（从 1 起）。

    买到的东西按类型分流：遗物进 relics、技能进 skills、装备进 equip。
    换下的装备进库存（``gear_stock``），不销毁。
    """
    session = shop_state(run)
    if session is None:
        return {"ok": False, "reason": "no_shop"}
    shelf = list(session.get("shelf") or [])
    pos = int(index) - 1
    coins, bought, code = buy(shelf, pos, int(run.coins))
    if code != "ok" or bought is None:
        return {"ok": False, "reason": code}
    session["shelf"] = shelf
    run.coins = coins

    kind = str(bought.get("kind"))
    spec = bought.get("spec") or {}
    replaced: str | None = None

    if kind == "relic":
        run.relics.append(str(spec.get("id")))
        replaced = None
    elif kind == "skill":
        from .flow import learn_skill
        from .skills import equip_skill

        skill_id = str(spec.get("id"))
        active, replaced = equip_skill(_active_skills(run), skill_id)
        run.skills = {**run.skills, "active": active, "passive": []}
        learn_skill(run, skill_id)
    elif kind == "gear":
        from .gear import equip as equip_gear

        equipped, replaced = equip_gear(run.equip, str(spec.get("id")))
        run.equip = equipped
        if replaced:
            run.pending.setdefault("gear_stock", []).append(replaced)

    return {
        "ok": True,
        "kind": kind,
        "spec": spec,
        "price": int(bought.get("price", 0) or 0),
        "replaced": replaced,
        "coins": run.coins,
    }


def leave_shop(
    run: Any,
) -> dict[str, Any]:
    """离店：丢弃货架，返回本局金币与剩几次刷新可刷。"""
    session = shop_state(run)
    if session is None:
        return {"ok": False, "reason": "no_shop"}
    refreshes = int(session.get("refreshes", 0) or 0)
    (run.pending or {}).pop("shop", None)
    return {"ok": True, "refreshes": refreshes, "coins": run.coins}


def _shelf_note(kind: str, spec: dict[str, Any]) -> str:
    """货架条目的说明：遗物 / 技能 / 装备各用各的口径。"""
    from .describe import gear_note, relic_note, skill_note

    if kind == "skill":
        return skill_note(spec)
    if kind == "gear":
        return gear_note(spec)
    return relic_note(spec)


def render_shop(session: dict[str, Any]) -> str:
    """把货架渲染成编号列表；空格子显式标出。"""
    shelf = list(session.get("shelf") or [])
    lines = ["🛒 商店"]
    for i, entry in enumerate(shelf, 1):
        spec = entry.get("spec") if isinstance(entry, dict) else None
        if not spec:
            lines.append(str(i) + ". —— 已售出 ——")
            continue
        name = str(spec.get("name") or spec.get("id"))
        tier = spec.get("tier", "?")
        price = int(entry.get("price", 0) or 0)
        kind = str(entry.get("kind") or "")
        note = _shelf_note(kind, spec)
        head = str(i) + ". " + name + "（T" + str(tier) + "）" + str(price) + " 金"
        # 说明单独一行：和价格挤在一行会到手机屏宽外，也看不清买了什么
        lines.append(head + ("\n　　" + note if note else ""))
    refreshes = int(session.get("refreshes", 0) or 0)
    next_cost = refresh_cost(refreshes)
    lines.append("刷新 " + str(next_cost) + " 金（第 " + str(refreshes + 1) + " 次）")
    return "\n".join(lines)
