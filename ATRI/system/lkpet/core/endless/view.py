"""无尽模式的指令层：``/宠物.无尽`` 的动作分发与回执渲染。

这一层只做三件事：读写 PETENDLESS、调 core 推进、把状态渲染成文本。
真正的规则全在 ``core/endless`` 里，这里不重复任何判定。
"""

from __future__ import annotations

from typing import Any

from ...utils.texts import command_start
from .codex import codex_paths, settle
from .floor import REGION_CN, floor_kind, offset_of, stage_of
from .run import RogueRun, advance, new_run, refresh_floor

ACTIONS = ("开始", "继续", "进度", "撤退")


def usage() -> str:
    cmd = f"{command_start()}宠物.无尽"
    return "用法：" + cmd + " " + "｜".join(ACTIONS)


def render_floor(run: RogueRun) -> str:
    """当前层的标题行：第几层、第几阶段、区域、层类型。"""
    region = REGION_CN.get(run.region, run.region)
    kind_cn = {
        "fixed": "固定战斗",
        "choice": "可选路线",
        "shop_gate": "战斗或商店",
        "elite": "精英",
        "boss": "首领",
    }
    kind = kind_cn.get(floor_kind(run.floor), floor_kind(run.floor))
    return (
        f"第 {run.floor} 层 · 阶段 {stage_of(run.floor)}·{offset_of(run.floor)}"
        f" · {region} · {kind}"
    )


def render_status(run: RogueRun) -> str:
    """一行状态：生命 / 魔力 / 金币 / 等级。"""
    return (
        f"❤ {run.hp}/{run.hp_max}  ◆ {run.mp}/{run.mp_max}  "
        f"🪙 {run.coins}  Lv.{run.progression.level}"
    )


def render_options(run: RogueRun) -> str:
    """把 pending 里的选项列成可回复的编号。"""
    from .events import render_event
    from .shop_session import render_shop, shop_state

    pending = run.pending or {}
    if pending.get("event"):
        return render_event(pending["event"])
    if shop_state(run):
        return (
            render_shop(shop_state(run) or {})
            + "\n回复编号买入；或回复「刷新」/「离开」"
        )
    options = pending.get("options") or []
    if not options:
        return ""
    kind = str(pending.get("type") or "")
    if kind.startswith("open_"):
        # 开局抽卡：选项本身就是遗物/技能定义，直接列名字
        lines = []
        for index, spec in enumerate(options, 1):
            label = str(spec.get("name") or spec.get("id") or "?")
            extra = ""
            if kind == "open_relic":
                extra = f"（T{spec.get('tier', '?')}）"
            lines.append(f"{index}. {label}{extra}")
        head = "🎁 选一件开局遗物：" if kind == "open_relic" else "📖 选一个开局技能："
        return head + "\n" + "\n".join(lines)
    names = {
        "battle": "战斗",
        "event": "事件",
        "elite_fight": "强化战斗",
        "rest": "休息补给",
        "shop": "商店",
    }
    lines = []
    for index, opt in enumerate(options, 1):
        kind = str(opt.get("kind") or "")
        lines.append(f"{index}. {names.get(kind, kind)}")
    return "\n".join(lines)


def render_progress(endless: Any) -> str:
    """历史进度与图鉴收集率。"""
    paths = codex_paths(endless)
    relic_have, relic_all = paths["relics"]
    skill_have, skill_all = paths["skills"]
    equip_have, equip_all = paths["equips"]
    return (
        f"最深第 {int(getattr(endless, 'best_floor', 0) or 0)} 层"
        f" · 共 {int(getattr(endless, 'total_runs', 0) or 0)} 局"
        f"\n📇 图鉴：遗物 {relic_have}/{relic_all}"
        f" · 技能 {skill_have}/{skill_all}"
        f" · 装备 {equip_have}/{equip_all}"
        f" · 事件 {paths['events']}"
    )


def render_summary(run: RogueRun) -> str:
    """开局回执：层、状态、选项三段拼一起。"""
    parts = [render_floor(run), render_status(run)]
    opts = render_options(run)
    if opts:
        parts.append(opts)
    return "\n".join(parts)


def render_reward(reward: dict[str, Any]) -> str:
    """一层打完的收获：经验/金币/遗物/回复，没变的项不写。"""
    parts: list[str] = []
    exp = int(reward.get("exp", 0) or 0)
    coins = int(reward.get("coins", 0) or 0)
    if exp:
        parts.append(f"经验 +{exp}")
    if coins:
        parts.append(f"金币 +{coins}")
    relic = (
        reward.get("drops", {}).get("relic")
        if isinstance(reward.get("drops"), dict)
        else None
    )
    if relic:
        parts.append(f"获得遗物 {relic}")
    heal = reward.get("heal") or {}
    if isinstance(heal, dict):
        if int(heal.get("hp", 0) or 0):
            parts.append(f"回复生命 {int(heal['hp'])}")
        if int(heal.get("mp", 0) or 0):
            parts.append(f"回复魔力 {int(heal['mp'])}")
    if int(reward.get("levels", 0) or 0):
        parts.append(f"升级 ×{int(reward['levels'])}")
    return "\n📦 " + "　".join(parts) if parts else ""


def start_run(seed: int) -> RogueRun:
    """开一局；调用方负责落盘与回执。"""
    return new_run(seed)


def next_floor(run: RogueRun) -> RogueRun:
    advance(run)
    return run


def reface(run: RogueRun) -> RogueRun:
    refresh_floor(run)
    return run


def finish(endless: Any, run: RogueRun) -> dict[str, Any]:
    """结算：调 codex.settle 写回，然后清空进行中的局。"""
    summary = settle(endless, run)
    return summary
