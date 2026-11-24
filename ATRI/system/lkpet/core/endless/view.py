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

ACTIONS = ("开始", "继续", "进度", "撤退", "状态", "装备", "技能", "遗物", "查看")


def usage() -> str:
    cmd = f"{command_start()}宠物.无尽"
    head = "用法：" + cmd + " " + "｜".join(ACTIONS)
    tail = "局内：查看 <名字>｜强化｜库存｜刷新｜离开"
    return head + "\n" + tail


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
    """一行状态：生命 / 金币 / 等级。

    魔力只活在战斗里，所以这里不显示——战斗中的蓝量由战报行的会话给出。
    """
    return f"❤ {run.hp}/{run.hp_max}  🪙 {run.coins}  Lv.{run.progression.level}"


ENDLESS_CMD = "宠物.无尽"
"""局内编号指令的相对路径（由 menus.cmd 补 command_start）。"""


def render_level_choice(run: RogueRun) -> str:
    """升级 4 选 1：逐项写出这次加多少，并让整行可点。"""
    from ..roguelike._common import line_link
    from .progression import LEVEL_KEYS, LEVEL_LABELS, gain_table

    gains = gain_table()
    left = len(run.progression.pending)
    lines = [f"🎖 升级！Lv.{run.progression.level}（还剩 {left} 次选择）"]
    for index, key in enumerate(LEVEL_KEYS, 1):
        body = f"{index}. {LEVEL_LABELS[key]} +{gains.get(key, 0)}"
        lines.append(line_link(body, f"{ENDLESS_CMD} {index}"))
    return "\n".join(lines)


def render_options(run: RogueRun) -> str:
    """把 pending 里的选项列成可回复的行（QQ 端整行可点）。"""
    from .events import render_event
    from .shop_session import render_shop, shop_state

    # 升级 4 选 1 优先：它必须在正文里出现，否则玩家看到的是楼层选项而按 1
    # 实际执行的是选加成（曾经就是这么错位的）
    if run.progression.pending:
        return render_level_choice(run)

    pending = run.pending or {}
    if pending.get("event"):
        return render_event(pending["event"])
    if shop_state(run):
        return (
            render_shop(shop_state(run) or {})
            + "\n回复编号买入；或回复「刷新」/「离开」"
        )
    if pending.get("session"):
        return render_battle(run)
    options = pending.get("options") or []
    if not options:
        return ""

    from ..roguelike._common import line_link, show_numbered_options

    numbered = show_numbered_options()
    ptype = str(pending.get("type") or "")
    rows: list[str] = []
    if ptype.startswith("open_"):
        head = "🎁 选一件开局遗物：" if ptype == "open_relic" else "📖 选一个开局技能："
        from .describe import relic_note, skill_note

        for index, spec in enumerate(options, 1):
            name = str(spec.get("name") or spec.get("id") or "?")
            tier = f"（T{spec.get('tier', '?')}）"
            note = relic_note(spec) if ptype == "open_relic" else skill_note(spec)
            body = f"{index}. {name}{tier}" if numbered else f"{name}{tier}"
            rows.append(line_link(body, f"{ENDLESS_CMD} {index}"))
            rows.append("　　" + note)
        rows.append("（想知道细节可以「查看 名字」）")
        return head + "\n" + "\n".join(rows)

    names = {
        "battle": "战斗",
        "event": "事件",
        "elite_fight": "强化战斗",
        "packs": "群怪（3 只·双倍奖励）",
        "rest": "休息补给",
        "shop": "商店",
        "forge": "强化（本层可反复强化）",
    }
    for index, opt in enumerate(options, 1):
        label = names.get(str(opt.get("kind") or ""), str(opt.get("kind") or ""))
        body = f"{index}. {label}" if numbered else label
        rows.append(line_link(body, f"{ENDLESS_CMD} {index}"))
    rows.append("查看：状态 / 装备 / 技能 / 遗物 · 详情用「查看 名字」")
    return "\n".join(rows)


_PACK_MARKS = "①②③④⑤⑥"
"""多只时的编号前缀：三个「100%」排在一起看不出谁是谁。"""


def enemy_line(run: RogueRun) -> str:
    """敌方血量行；群怪只列存活的那几只，单只只写一行。"""
    pending = run.pending or {}
    data = pending.get("session") or {}
    monster = pending.get("monster") or {}
    all_units = list(data.get("units") or [])
    if not all_units:
        return ""
    # 只算「本体 + 群怪成员」：召唤物是同伙不是同群，算进去会让单怪看起来像群怪。
    units = [
        u for u in all_units if bool(u.get("pack")) or bool(u.get("primary"))
    ] or all_units
    total = len(units)
    # 只列存活：倒下的不占行，玩家只关心还剩几只、各自还有多少。
    alive = [u for u in units if int((u.get("unit") or {}).get("hp") or 0) > 0]
    name = str(monster.get("name") or "敌人")
    if total == 1:
        rec = units[0]
        hp = max(0, int((rec.get("unit") or {}).get("hp") or 0))
        cap = int(rec.get("hp_cap") or 0)
        bar = _gauge_label(rec) if hp > 0 else "已倒下"
        return f"👹 {name}  {hp}/{cap}  行动条 {bar}"

    lines = [f"🐝 群怪（存活 {len(alive)}/{total}）"]
    for rec in units:
        if int((rec.get("unit") or {}).get("hp") or 0) <= 0:
            continue
        hp = int((rec.get("unit") or {}).get("hp") or 0)
        cap = int(rec.get("hp_cap") or 0)
        unit_name = str((rec.get("unit") or {}).get("name") or name)
        lines.append(f"👹 {unit_name}  {hp}/{cap}  行动条 {_gauge_label(rec)}")
    return "\n".join(lines)


def _gauge_label(rec: dict[str, Any]) -> str:
    """单只敌方的行动条百分比。"""
    pct = min(100, max(0, round(float(rec.get("gauge") or 0.0))))
    return f"{pct}%"


def _battle_mp_line(run: RogueRun) -> str:
    """战斗中的魔力行：蓝只在战斗里存在，所以也只在这里显示。"""
    data = (run.pending or {}).get("session") or {}
    if not data:
        return ""
    have = int(data.get("pet_mp", 0) or 0)
    cap = int(data.get("mp_cap", 0) or 0)
    return f"◆ {have}/{cap}" if cap else ""


def monster_trait_line(run: RogueRun) -> str:
    """敌方特点一行（棘刺 / 爆裂 / 吸血…）；没有特点返回空串。"""
    monster = (run.pending or {}).get("monster") or {}
    ids = [str(t) for t in monster.get("traits") or []]
    if not ids:
        return ""
    from ...utils.texts import text

    labels = [text(f"rogue.traits.{t}", t) for t in ids]
    return text(
        "rogue.monster_trait_line", "🔹 特点：{traits}", traits="，".join(labels)
    )


def render_battle(run: RogueRun, *, log_limit: int = 8) -> str:
    """战斗中的战报 + 状态行 + 可选动作（整行可点，与肉鸽的「迎战」同款）。

    战报取自 ``pending["battle_log"]``——那是**这一步**产生的行，不是整场：
    ATB 一场要打十几步，全量重放会把正文撑爆，也看不出这次发生了什么。
    """
    from ..roguelike._common import line_link, show_numbered_options
    from .encounter import battle_actions

    numbered = show_numbered_options()
    head: list[str] = []
    raw = (run.pending or {}).get("battle_log") or []
    shown = [tuple(x) for x in raw][-log_limit:]
    if shown:
        head.append("🗒 战报：")
        head.extend(line for _, line in shown)

    head.extend(
        [
            monster_trait_line(run),
            enemy_line(run),
            render_status(run),
            _battle_mp_line(run),
        ]
    )
    head = [x for x in head if x]

    rows: list[str] = []
    for act in battle_actions(run):
        key = str(act.get("key") or "")
        label = str(act.get("label") or key)
        body = f"{key}. {label}" if numbered else label
        rows.append(line_link(body, f"{ENDLESS_CMD} {key}"))
    if not rows:
        return "\n".join(head)
    return "\n".join([*head, "轮到你了：", *rows])


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
    drops = reward.get("drops") if isinstance(reward.get("drops"), dict) else {}
    relic_id = drops.get("relic")
    if relic_id:
        from .describe import relic_note
        from .relics import relic_specs

        spec = relic_specs().get(str(relic_id)) or {}
        name = str(spec.get("name") or relic_id)
        note = relic_note(spec) if spec else ""
        parts.append(f"获得遗物 {name}" + (f"（{note}）" if note else ""))
    gear_id = drops.get("gear")
    if gear_id:
        from .describe import gear_note
        from .gear import gear_specs

        spec = gear_specs().get(str(gear_id)) or {}
        name = str(spec.get("name") or gear_id)
        note = gear_note(spec) if spec else ""
        where = (
            "已穿上" if drops.get("auto_equipped") else "已入库存，可在「装备」里换上"
        )
        parts.append(f"获得装备 {name}（{where}）" + (f"\n　　{note}" if note else ""))
    heal = reward.get("heal") or {}
    if isinstance(heal, dict):
        if int(heal.get("hp", 0) or 0):
            parts.append(f"回复生命 {int(heal['hp'])}")
        if int(heal.get("mp", 0) or 0):
            parts.append(f"回复魔力 {int(heal['mp'])}")
    if int(reward.get("levels", 0) or 0):
        parts.append(f"升级 ×{int(reward['levels'])}")
    return "📦 " + "　".join(parts) if parts else ""


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
