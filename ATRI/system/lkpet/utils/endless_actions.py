"""无尽模式的局内按钮。

与肉鸽的 rogue_actions 同职责：把当前 pending 翻成可点的指令。
无尽没有独立的状态机类，所以直接按 pending 的 type 分派。
"""

from __future__ import annotations

from typing import Any

from .reply import Choice

MAX_CHOICES = 6
"""一次最多给几个按钮。"""


def _label(spec: dict[str, Any]) -> str:
    return str(spec.get("name") or spec.get("id") or "?")


def choices_from_endless(run: Any) -> list[Choice]:
    """从 RogueRun 算按钮；拿不到就返回空列表。"""
    from .menus import cmd

    pending = dict(getattr(run, "pending", None) or {})
    if not pending:
        return []

    kind = str(pending.get("type") or "")
    options = list(pending.get("options") or [])

    if getattr(run, "progression", None) and run.progression.pending:
        from ..core.endless.progression import LEVEL_KEYS, LEVEL_LABELS, gain_table

        gains = gain_table()
        # 与正文的 4 选 1 对齐：这里给加成本身，不是楼层选项
        return [
            Choice(
                f"{LEVEL_LABELS[key]} +{gains.get(key, 0)}",
                cmd(f"宠物.无尽 {index}"),
            )
            for index, key in enumerate(LEVEL_KEYS, 1)
        ]

    if pending.get("session"):
        from ..core.endless import battle_actions

        return [
            Choice(
                str(act.get("label") or act.get("key")), cmd(f"宠物.无尽 {act['key']}")
            )
            for act in battle_actions(run)[:MAX_CHOICES]
        ]

    # 非战斗面板：没有待答选项时给「状态 / 装备 / 技能 / 遗物」入口
    if not options and not pending.get("event") and not pending.get("shop"):
        from ..core.endless import PANELS

        return [Choice(name, cmd(f"宠物.无尽 {name}")) for name in PANELS]

    event = pending.get("event")
    if isinstance(event, dict) and event.get("choices"):
        # 事件卡的按钮是**选项本身**，不是楼层选项——两者同时在 pending 里，
        # 不先判事件会让底部给出「战斗 / 事件」这种已经过时的一层。
        return [
            Choice(
                str(choice.get("text") or "?")[:12],
                cmd(f"宠物.无尽 {index}"),
            )
            for index, choice in enumerate(event["choices"][:MAX_CHOICES], 1)
        ]

    if pending.get("shop"):
        shelf = list((pending.get("shop") or {}).get("shelf") or [])
        out = [
            Choice(
                _label(entry.get("spec") or {}),
                cmd(f"宠物.无尽 {index}"),
            )
            for index, entry in enumerate(shelf[:MAX_CHOICES], 1)
            if (entry.get("spec") or {})
        ]
        # 刷新/离开永远保留：货架位再多也要能退出这家店
        tail = [
            Choice("刷新", cmd("宠物.无尽 刷新")),
            Choice("离开", cmd("宠物.无尽 离开")),
        ]
        return out[: MAX_CHOICES - len(tail)] + tail

    if kind.startswith("open_"):
        return [
            Choice(_label(spec), cmd(f"宠物.无尽 {index}"))
            for index, spec in enumerate(options[:MAX_CHOICES], 1)
            if isinstance(spec, dict)
        ]
    if options:
        names = {
            "battle": "战斗",
            "event": "事件",
            "elite_fight": "强化战斗",
            "rest": "休息补给",
            "shop": "商店",
        }
        from ..core.endless import PANELS

        picked = [
            Choice(
                names.get(str(opt.get("kind") or ""), str(opt.get("kind") or "?")),
                cmd(f"宠物.无尽 {index}"),
            )
            for index, opt in enumerate(options[:4], 1)
        ]
        # 面板入口跟在楼层选项后面：一层最多 3 个选项，剩下的位给最常用的三个
        tail = [
            Choice(name, cmd(f"宠物.无尽 {name}"))
            for name in ("状态", "装备", "技能")
            if name in PANELS
        ]
        return (picked + tail)[:MAX_CHOICES]
    return []
