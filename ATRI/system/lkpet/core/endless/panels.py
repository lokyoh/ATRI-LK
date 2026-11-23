"""无尽模式的面板：状态 / 装备 / 技能 / 遗物。

面板**只能在非战斗状态打开**：战斗中每条指令都要推进战斗，插进面板会让
玩家以为还有别的操作。

每个面板都实现自己能做的操作，操作走 ``宠物.无尽 <面板> <动作> <序号>``：
面板自己解析，不进楼层状态机。
"""

from __future__ import annotations

from typing import Any

from .describe import _flat_pct, attr_label, attr_line, attr_value, describe_attrs
from .gear import (
    decompose,
    decompose_value,
    gear_effects,
    gear_specs,
    stock_of,
    take_from_stock,
    unequip,
)
from .relics import describe_sets, relic_specs, set_bonuses
from .skills import (
    ELEMENT_CN,
    SERIES_CN,
    active_slots,
    equip_skill,
    skill_specs,
)

SLOT_CN = {"weapon": "武器", "armor": "防具", "accessory": "饰品"}
"""槽位中文名。"""

PANELS = ("状态", "装备", "技能", "遗物", "强化")
"""可打开的面板名。"""

MAX_STOCK_ROWS = 20
"""装备面板一次最多列几件库存：再多会撑到单条消息上限，正文被截断。"""

MAX_BENCH_ROWS = 20
"""技能面板一页列几个未装技能；再多翻页。"""


PRIMARY_KEYS = (
    ("atk", "攻击"),
    ("def", "防御"),
    ("spd", "速度"),
    ("hp", "生命上限"),
    ("mp", "魔力上限"),
)
"""状态面板先列这五项：它们是一局的骨架，其余都算加成。"""


def in_battle(run: Any) -> bool:
    """战斗进行中就不能打开面板。"""
    return bool((getattr(run, "pending", None) or {}).get("session"))


def busy() -> str:
    return "⚔️ 战斗中不能查看面板，先把这一场打完。"


def _nonzero(value: Any) -> bool:
    if isinstance(value, dict):
        return any(float(v) for v in value.values())
    if isinstance(value, int | float):
        return float(value) != 0
    return bool(value)


def _active_ids(run: Any) -> list[str]:
    return [str(s) for s in ((run.skills or {}).get("active") or [])]


def _owned_relics(run: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for relic_id in run.relics or []:
        key = str(relic_id)
        out[key] = out.get(key, 0) + 1
    return out


def _equipped_count(run: Any) -> int:
    return sum(len(ids or []) for ids in (run.equip or {}).values())


# ---------------------------------------------------------------- 状态


def render_status(run: Any) -> str:
    """状态面板：主属性 + 全部加成 + 生命魔力金币。"""
    from .flow import combat_attrs

    attrs = combat_attrs(run)
    lines = [
        "📊 状态",
        f"Lv.{run.progression.level} · 金币 {run.coins}",
        f"生命 {run.hp}/{run.hp_max}",
    ]
    for key, label in PRIMARY_KEYS:
        lines.append(f"{label} {attr_value(key, attrs.get(key, 0))}")

    bonus = [
        f"{attr_label(key)} {attr_value(key, value)}"
        for key, value in attrs.items()
        if key not in dict(PRIMARY_KEYS) and _nonzero(value)
    ]
    if bonus:
        lines.append("加成：" + "，".join(bonus))
    else:
        lines.append("加成：暂无")
    lines.append(f"遗物 {len(run.relics)} 件 · 装备 {_equipped_count(run)} 件")
    lines.append("暴击伤害基准 1.8 倍（装备与遗物在此基础上叠加）")
    return "\n".join(lines)


# ---------------------------------------------------------------- 装备


def gear_line(spec: dict[str, Any]) -> str:
    """一件装备的一行：槽位 + 档位 + 名字 + 属性。"""
    slot = SLOT_CN.get(str(spec.get("slot")), str(spec.get("slot")))
    tier = int(spec.get("tier", 1) or 1)
    name = str(spec.get("name") or spec.get("id"))
    note = "，".join(attr_line(gear_effects(spec)))
    return f"{slot} T{tier} {name}" + (f" · {note}" if note else "")


def render_gear(run: Any) -> str:
    """装备面板：已穿四槽 + 库存。

    正文保持纯文本，只在行尾挂**短指令**（穿上 / 卸下 / 分解）。
    整行都做成链接时每条要编码整段标签，六件库存就能撑爆单条上限，
    正文会被截断——反而看不到刚分解后的库存。
    """
    from ..roguelike._common import line_link

    base = "宠物.无尽"
    specs = gear_specs()
    lines = ["🛡 装备"]
    for slot in ("weapon", "armor", "accessory"):
        ids = list((run.equip or {}).get(slot) or [])
        if not ids:
            lines.append(f"{SLOT_CN[slot]}：空槽")
            continue
        lines.append(f"{SLOT_CN[slot]}：")
        for gear_id in ids:
            spec = specs.get(str(gear_id)) or {}
            lines.append(
                "  "
                + gear_line(spec)
                + "　"
                + line_link("卸下", f"{base} 装备 卸下 {SLOT_CN[slot]}")
            )

    stock = stock_of(run)
    if stock:
        lines.append("库存（点「穿上 / 分解」执行）：")
        for index, gear_id in enumerate(stock[:MAX_STOCK_ROWS], 1):
            spec = specs.get(str(gear_id)) or {}
            # 分解退还全部强化费：强化过的装备不该按裸价回收
            from .gear import gear_enhance_of

            value = decompose_value(spec, gear_enhance_of(run, gear_id))
            lines.append(
                f"  {index}. "
                + gear_line(spec)
                + "　"
                + line_link("穿上", f"{base} 装备 穿上 {index}")
                + " "
                + line_link(f"分解 {value} 金", f"{base} 装备 分解 {index}")
            )
        rest = len(stock) - MAX_STOCK_ROWS
        if rest > 0:
            lines.append(f"  …… 还有 {rest} 件，先处理上面的再回来看")
    else:
        lines.append("库存：空")
    return "\n".join(lines)


def gear_actions(run: Any) -> list[tuple[str, str]]:
    """装备面板的按钮：(完整指令, 显示名)。"""
    from ...utils.menus import cmd

    base = "宠物.无尽"
    specs = gear_specs()
    stock = stock_of(run)
    out: list[tuple[str, str]] = []

    for index, gear_id in enumerate(stock[:3], 1):
        name = str((specs.get(gear_id) or {}).get("name") or gear_id)
        out.append((cmd(f"{base} 装备 穿上 {index}"), f"穿 {name}"))
    for index, gear_id in enumerate(stock[:2], 1):
        name = str((specs.get(gear_id) or {}).get("name") or gear_id)
        out.append((cmd(f"{base} 装备 分解 {index}"), f"分解 {name}"))
    for slot in ("weapon", "armor", "accessory"):
        if not (run.equip or {}).get(slot):
            continue
        out.append((cmd(f"{base} 装备 卸下 {SLOT_CN[slot]}"), f"卸下{SLOT_CN[slot]}"))
    return out[:8]


def _stock_by_number(run: Any, number: int) -> str | None:
    stock = stock_of(run)
    if not 1 <= int(number) <= len(stock):
        return None
    return stock[int(number) - 1]


def gear_action(run: Any, action: str, arg: str) -> tuple[str, bool]:
    """装备面板的操作：穿上 N / 卸下 槽位 / 分解 N。"""
    specs = gear_specs()
    if action == "穿上":
        gear_id = _stock_by_number(run, int(arg))
        if not gear_id:
            return "库存里没有这个编号。", False
        spec = specs.get(gear_id) or {}
        if not take_from_stock(run, gear_id):
            return "这件不在库存里。", False
        return f"✅ 已穿上 {spec.get('name') or gear_id}。\n" + render_gear(run), True

    if action == "卸下":
        slot = {v: k for k, v in SLOT_CN.items()}.get(arg)
        if not slot:
            return "槽位写错，可选：武器 / 防具 / 饰品。", False
        equipped, removed = unequip(run.equip, slot)
        if not removed:
            return f"{arg} 是空槽。", False
        run.equip = equipped
        run.pending.setdefault("gear_stock", []).append(removed)
        name = (specs.get(removed) or {}).get("name") or removed
        return f"✅ 已卸下 {name}，进了库存。\n" + render_gear(run), True

    if action == "分解":
        gear_id = _stock_by_number(run, int(arg))
        if not gear_id:
            return "库存里没有这个编号。", False
        value = decompose(run, gear_id)
        if not value:
            return "这件分解不了。", False
        return f"♻️ 分解回收 {value} 金币。\n" + render_gear(run), True

    return (
        "装备面板的动作用法：装备 / 装备 穿上 N / 装备 卸下 武器|防具|饰品 / 装备 分解 N",
        False,
    )


# ---------------------------------------------------------------- 技能


def skill_line(spec: dict[str, Any], times: int = 0) -> str:
    """一个技能的一行：档位 + 名字 + 效果 + 强化次数。"""
    from .describe import skill_note

    tier = int(spec.get("tier", 1) or 1)
    name = str(spec.get("name") or spec.get("id"))
    plus = f"，强化×{times}" if times else ""
    return f"T{tier} {name} · {skill_note(spec)}{plus}"


def _bench_page(run: Any, page: int) -> tuple[list[str], int, int]:
    """未装技能按页切片：返回 (本页 id, 当前页, 总页数)。"""
    from .flow import known_skills

    specs = skill_specs()
    active = _active_ids(run)
    bench = [s for s in known_skills(run) if s not in active and s in specs]
    pages = max(1, -(-len(bench) // MAX_BENCH_ROWS))
    page = min(max(1, int(page)), pages)
    start = (page - 1) * MAX_BENCH_ROWS
    return bench[start : start + MAX_BENCH_ROWS], page, pages


def render_skills(run: Any, *, page: int = 1) -> str:
    """技能面板：已装槽位 + 未装的已学技能（分页）。

    已学技能会累积到几十个，一屏塞不下；页码写在标题上，翻页靠「技能 页 N」。
    """
    from ..roguelike._common import line_link
    from .flow import known_skills

    base = "宠物.无尽"
    specs = skill_specs()
    enhance = dict((run.pending or {}).get("enhance") or {})
    active = _active_ids(run)
    lines = [f"📖 技能（槽位 {len(active)}/{active_slots()}）"]
    for index, skill_id in enumerate(active, 1):
        spec = specs.get(str(skill_id)) or {}
        times = int(enhance.get(str(skill_id), 0) or 0)
        lines.append(
            f"  {index}. "
            + skill_line(spec, times)
            + "　"
            + line_link("换下", f"{base} 技能 换下 {index}")
        )

    total = len([s for s in known_skills(run) if s not in active and s in specs])
    if total:
        shown, cur, pages = _bench_page(run, page)
        head = f"未装（{total} 个"
        head += f"，第 {cur}/{pages} 页）：" if pages > 1 else "）："
        lines.append(head)
        offset = (cur - 1) * MAX_BENCH_ROWS
        for index, skill_id in enumerate(shown, 1):
            spec = specs.get(skill_id) or {}
            times = int(enhance.get(skill_id, 0) or 0)
            lines.append(
                f"  {offset + index}. "
                + skill_line(spec, times)
                + "　"
                + line_link("换上", f"{base} 技能 换上 {offset + index}")
            )
        if pages > 1:
            nav: list[str] = []
            if cur > 1:
                nav.append(line_link("上一页", f"{base} 技能 页 {cur - 1}"))
            if cur < pages:
                nav.append(line_link("下一页", f"{base} 技能 页 {cur + 1}"))
            lines.append("　".join(nav))
    return "\n".join(lines)


def skill_action(run: Any, action: str, arg: str, *, page: int = 1) -> tuple[str, bool]:
    """技能面板的操作：换上 N / 换下 N / 页 N。"""
    specs = skill_specs()
    active = _active_ids(run)
    if action == "页":
        # 只换页，不改任何状态
        return render_skills(run, page=int(arg or 1)), False

    if action == "换下":
        if not 1 <= int(arg) <= len(active):
            return "已装的技能没有这个编号。", False
        skill_id = active.pop(int(arg) - 1)
        run.skills = {**run.skills, "active": active, "passive": []}
        name = (specs.get(skill_id) or {}).get("name") or skill_id
        return f"✅ 已换下 {name}。\n" + render_skills(run, page=page), True

    if action == "换上":
        from .flow import known_skills

        # 编号是**全局序号**（跨页连续），不是当前页内的序号
        bench = [s for s in known_skills(run) if s not in active and s in specs]
        if not 1 <= int(arg) <= len(bench):
            return "未装列表里没有这个编号。", False
        skill_id = bench[int(arg) - 1]
        new_active, replaced = equip_skill(active, skill_id)
        run.skills = {**run.skills, "active": new_active, "passive": []}
        name = (specs.get(skill_id) or {}).get("name") or skill_id
        tail = ""
        if replaced:
            old = (specs.get(replaced) or {}).get("name") or replaced
            tail = f"，换下 {old}"
        return f"✅ 已换上 {name}{tail}。\n" + render_skills(run, page=page), True

    return "技能面板的动作用法：技能 / 技能 换上 N / 技能 换下 N / 技能 页 N", False


def relic_line(spec: dict[str, Any], dup: int = 1) -> str:
    """一件遗物的标题行；属性由下一行的 :func:`relic_note` 给出，不重复。"""
    tier = int(spec.get("tier", 1) or 1)
    name = str(spec.get("name") or spec.get("id"))
    count = f" ×{dup}" if dup > 1 else ""
    return f"T{tier} {name}{count}"


def render_relics(run: Any) -> str:
    """遗物面板：已持有（按套分组）+ 套装进度。"""
    specs = relic_specs()
    owned = _owned_relics(run)
    lines = [f"🎁 遗物（{len(run.relics)} 件）"]
    groups: dict[str, list[tuple[dict[str, Any], int]]] = {"散件": []}
    for relic_id, dup in owned.items():
        spec = specs.get(relic_id) or {"id": relic_id}
        groups.setdefault(str(spec.get("set") or "散件"), []).append((spec, dup))
    for key, items in groups.items():
        if not items:
            continue
        from .describe import relic_note
        from .relics import set_meta

        label = str((set_meta().get(key) or {}).get("name") or key)
        lines.append(f"{label}：")
        for spec, dup in items:
            lines.append("  " + relic_line(spec, dup))
            lines.append("　　" + relic_note(spec))

    sets = describe_sets(owned)
    if sets:
        lines.append("套装进度：")
        for _key, count, name in sets:
            lines.append(f"  {name} {count}/8")
    bonus = set_bonuses(owned)
    if bonus:
        note = "，".join(attr_line(bonus))
        lines.append("套装加成：" + note)
    return "\n".join(lines)


def skill_actions(run: Any) -> list[tuple[str, str]]:
    """技能面板的按钮：标签带技能名，不带编号。"""
    from ...utils.menus import cmd
    from .flow import known_skills

    base = "宠物.无尽"
    specs = skill_specs()
    active = _active_ids(run)
    bench = [s for s in known_skills(run) if s not in active and s in specs]
    out: list[tuple[str, str]] = []

    for index, skill_id in enumerate(bench[:3], 1):
        name = str((specs.get(skill_id) or {}).get("name") or skill_id)
        out.append((cmd(f"{base} 技能 换上 {index}"), f"换 {name}"))
    for index, skill_id in enumerate(active, 1):
        name = str((specs.get(skill_id) or {}).get("name") or skill_id)
        out.append((cmd(f"{base} 技能 换下 {index}"), f"卸 {name}"))
    return out[:8]


def relic_actions(run: Any) -> list[tuple[str, str]]:
    """遗物面板没有可执行操作：拿到即永久生效。"""
    return []


def find_spec(name: str) -> tuple[str, dict[str, Any]] | None:
    """按名字或 id 找一件技能 / 装备 / 遗物。"""
    needle = str(name or "").strip()
    if not needle:
        return None
    for kind, table in (
        ("技能", skill_specs()),
        ("装备", gear_specs()),
        ("遗物", relic_specs()),
    ):
        for spec in table.values():
            if needle in (str(spec.get("name") or ""), str(spec.get("id") or "")):
                return kind, spec
    return None


DIR_CN = {
    "atk": "攻击力",
    "def": "防御力",
    "spd": "速度",
    "mp": "魔力上限",
    "hp": "生命上限",
}
"""转伤属性（§8.5）：让非攻击构筑也有输出。"""


def _dir_note(spec: dict[str, Any]) -> str:
    """这条技能吃哪个属性；辅助与防御类不吃属性，直说。"""
    series = str(spec.get("series") or "")
    if series in ("support", "guard"):
        return "不吃属性（辅助 / 防御向，不造成伤害）"
    key = str(spec.get("dir") or "atk")
    return DIR_CN.get(key, attr_label(key))


def _target_note(spec: dict[str, Any]) -> str:
    """技能目标；``self`` 是给自己上效果，不是打人。"""
    raw = str(spec.get("target") or "single")
    return {"self": "自身", "all": "敌方全体"}.get(raw, "单个敌人")


def _pct(value: float) -> str:
    return str(round((float(value) - 1.0) * 100))


def _skill_effect_note(spec: dict[str, Any]) -> str:
    """技能实际做什么——数值全取自数据，改 YAML 这里跟着变。"""
    parts: list[str] = []
    shield = float(spec.get("shield", 0.0) or 0.0)
    shield_flat = float(spec.get("shield_flat", 0.0) or 0.0)
    if shield or shield_flat:
        parts.append("护盾 " + _flat_pct(shield_flat, shield))
    heal = float(spec.get("heal", 0.0) or 0.0)
    heal_flat = float(spec.get("heal_flat", 0.0) or 0.0)
    if heal or heal_flat:
        parts.append("回复 " + _flat_pct(heal_flat, heal))
    for key, label in (
        ("atk_mult", "攻击"),
        ("def_mult", "防御"),
        ("spd_mult", "速度"),
    ):
        if key in spec:
            parts.append(f"{label} +{_pct(spec[key])}%")
    rounds = int(spec.get("rounds", 0) or 0)
    if rounds and any(k.endswith("_mult") for k in spec):
        parts.append(f"持续 {rounds} 回合")
    if not parts:
        power = int(spec.get("power", 0) or 0)
        if power:
            return f"效果：造成约 {round(power / 30.0, 2)} 倍普攻的伤害"
        return "效果：无"
    return "效果：" + "，".join(parts) + "（不造成伤害）"


def render_detail(kind: str, spec: dict[str, Any]) -> str:
    """「查看」的展开：一件东西的完整介绍。"""
    lines = [f"🔍 {kind}：{spec.get('name') or spec.get('id')}"]
    tier = spec.get("tier")
    if tier:
        lines.append(f"档位 T{tier}")
    if kind == "技能":
        elem = ELEMENT_CN.get(str(spec.get("element")), str(spec.get("element")))
        series = SERIES_CN.get(str(spec.get("series")), str(spec.get("series")))
        lines.append(f"{elem}·{series}")
        lines.append(_skill_effect_note(spec))
        lines.append(
            f"耗魔 {spec.get('mp_cost', 0) or '无'} · 冷却 {spec.get('cooldown', 0) or '无'} 次出手"
        )
        lines.append("目标：" + _target_note(spec))
    elif kind == "装备":
        slot = SLOT_CN.get(str(spec.get("slot")), str(spec.get("slot")))
        lines.append(f"槽位：{slot}")
        special = spec.get("special") or {}
        if special.get("desc"):
            lines.append(f"特殊：{special['desc']}")
        lines.extend(describe_attrs(gear_effects(spec)))
    else:
        set_key = str(spec.get("set") or "")
        if set_key:
            from .relics import set_meta

            label = str((set_meta().get(set_key) or {}).get("name") or set_key)
            lines.append(f"套装：{label}")
        else:
            lines.append("套装：散件")
        lines.extend(describe_attrs(dict(spec.get("effect") or {})))
        set_key = str(spec.get("set") or "")
        if set_key:
            bonus = set_bonuses({str(spec.get("id")): 1})
            if bonus:
                lines.append("占 1 件时的套装加成：" + "，".join(attr_line(bonus)))
    return "\n".join(lines)


# ---------------------------------------------------------------- 强化


def _enhance_targets(run: Any) -> list[tuple[str, str]]:
    """可强化的对象：已装的 4 个技能 + 已穿的全部装备。"""
    out: list[tuple[str, str]] = []
    for skill_id in _active_ids(run):
        out.append(("skill", str(skill_id)))
    for slot in ("weapon", "armor", "accessory"):
        for gear_id in list((run.equip or {}).get(slot) or []):
            out.append(("gear", str(gear_id)))
    return out


def render_enhance(run: Any) -> str:
    """强化面板：列出可强化对象、当前等级、下一级价格与退款额。"""
    from ..roguelike._common import line_link
    from .gear import (
        GEAR_ENHANCE_MAX,
        gear_enhance_cost,
        gear_specs,
        gear_total_enhance_cost,
    )
    from .skills import ENHANCE_MAX, enhance_cost, total_enhance_cost

    base = "宠物.无尽"
    if in_battle(run):
        return busy()
    sk_specs = skill_specs()
    gr_specs = gear_specs()
    sk_times = dict((run.pending or {}).get("enhance") or {})
    gr_times = dict((run.pending or {}).get("gear_enhance") or {})
    lines = [f"🔧 强化（持有 {run.coins} 金）"]
    rows = 0
    targets = _enhance_targets(run)
    if not targets:
        lines.append("没有可强化的技能或装备：先装上再来。")
    for kind, item_id in targets:
        if kind == "skill":
            spec = sk_specs.get(item_id) or {}
            times = int(sk_times.get(item_id, 0) or 0)
            cap = ENHANCE_MAX
            cost = enhance_cost(int(spec.get("tier", 1) or 1), times)
            refund = total_enhance_cost(int(spec.get("tier", 1) or 1), times)
            label = f"技能 {spec.get('name') or item_id}"
        else:
            spec = gr_specs.get(item_id) or {}
            times = int(gr_times.get(item_id, 0) or 0)
            cap = GEAR_ENHANCE_MAX
            cost = gear_enhance_cost(int(spec.get("tier", 1) or 1), times)
            refund = gear_total_enhance_cost(int(spec.get("tier", 1) or 1), times)
            label = f"装备 {spec.get('name') or item_id}"
        rows += 1
        head = f"  {rows}. {label}  +{times}/{cap}"
        if times:
            head += f"（取消退 {refund} 金）"
        lines.append(head)
        if times >= cap:
            lines.append("　　　已满级")
            continue
        lines.append(
            "　　　"
            + line_link(f"强化 +1（{cost} 金）", f"{base} 强化 强化 {kind} {rows}")
            + (
                " "
                + line_link(
                    f"取消强化（退 {refund} 金）", f"{base} 强化 取消 {kind} {rows}"
                )
                if times
                else ""
            )
        )
    lines.append("只能给已装的技能与已穿的装备强化；换下/卸下后仍保留强化等级。")
    return "\n".join(lines)


def enhance_actions(run: Any) -> list[tuple[str, str]]:
    """强化面板的按钮：给前几项各一个「强化 +1」。"""
    from ...utils.menus import cmd
    from .gear import GEAR_ENHANCE_MAX, gear_enhance_cost, gear_specs
    from .skills import ENHANCE_MAX, enhance_cost

    base = "宠物.无尽"
    sk_specs = skill_specs()
    gr_specs = gear_specs()
    sk_times = dict((run.pending or {}).get("enhance") or {})
    gr_times = dict((run.pending or {}).get("gear_enhance") or {})
    out: list[tuple[str, str]] = []
    for index, (kind, item_id) in enumerate(_enhance_targets(run), 1):
        if kind == "skill":
            spec = sk_specs.get(item_id) or {}
            times = int(sk_times.get(item_id, 0) or 0)
            if times >= ENHANCE_MAX:
                continue
            cost = enhance_cost(int(spec.get("tier", 1) or 1), times)
        else:
            spec = gr_specs.get(item_id) or {}
            times = int(gr_times.get(item_id, 0) or 0)
            if times >= GEAR_ENHANCE_MAX:
                continue
            cost = gear_enhance_cost(int(spec.get("tier", 1) or 1), times)
        name = str(spec.get("name") or item_id)
        out.append(
            (cmd(f"{base} 强化 强化 {kind} {index}"), f"强化 {name}（{cost} 金）")
        )
    return out[:6]


def render_panel(run: Any, name: str, *, page: int = 1) -> str:
    """按名字打开一个面板。"""
    if str(name) == "技能":
        return render_skills(run, page=page)
    if str(name) == "强化":
        return render_enhance(run)
    table = {
        "状态": render_status,
        "装备": render_gear,
        "遗物": render_relics,
    }
    render = table.get(str(name))
    if render is None:
        return "没有这个面板，可选：" + " / ".join(PANELS)
    return render(run)


def panel_actions(run: Any, name: str) -> list[tuple[str, str]]:
    """一个面板底部的按钮：(完整指令, 显示名)。"""
    from ...utils.menus import cmd

    base = "宠物.无尽"
    jump = [(cmd(f"{base} {other}"), other) for other in PANELS if other != name]
    if name == "装备":
        return gear_actions(run)[:6] + jump[:2]
    if name == "技能":
        return skill_actions(run)[:6] + jump[:2]
    if name == "强化":
        return enhance_actions(run)[:6] + jump[:2]
    return jump[:4]


def enhance_action(run: Any, action: str, kind: str, arg: str) -> tuple[str, bool]:
    """强化面板的操作：``强化 skill|gear N`` / ``取消 skill|gear N``。"""
    from .gear import (
        GEAR_ENHANCE_MAX,
        gear_enhance_cost,
        gear_specs,
        gear_total_enhance_cost,
    )
    from .skills import ENHANCE_MAX, enhance_cost, skill_specs, total_enhance_cost

    if in_battle(run):
        return busy(), False
    if kind not in ("skill", "gear"):
        return "强化对象写错，可选：skill / gear。", False
    targets = _enhance_targets(run)
    if not arg.isdigit() or not 1 <= int(arg) <= len(targets):
        return "编号超出范围。", False
    target_kind, item_id = targets[int(arg) - 1]
    if target_kind != kind:
        return "这个编号不是该类型的对象。", False
    spec = (skill_specs() if kind == "skill" else gear_specs()).get(item_id) or {}
    tier = int(spec.get("tier", 1) or 1)
    table_key = "enhance" if kind == "skill" else "gear_enhance"
    table = dict(run.pending.get(table_key) or {})
    times = int(table.get(item_id, 0) or 0)
    name = str(spec.get("name") or item_id)
    cap = ENHANCE_MAX if kind == "skill" else GEAR_ENHANCE_MAX
    if action == "强化":
        if times >= cap:
            return "已经强化到上限了。", False
        cost = (enhance_cost if kind == "skill" else gear_enhance_cost)(tier, times)
        if run.coins < cost:
            return f"金币不够（需要 {cost}）。", False
        run.coins -= cost
        table[item_id] = times + 1
        run.pending[table_key] = table
        return (
            f"🔧 {name} 强化到 {times + 1}/{cap}，花费 {cost} 金，剩余 {run.coins} 金。\n"
            + render_enhance(run),
            True,
        )
    if action == "取消":
        if times <= 0:
            return "这件还没强化过。", False
        refund = (
            total_enhance_cost(tier, times)
            if kind == "skill"
            else gear_total_enhance_cost(tier, times)
        )
        run.coins += refund
        table.pop(item_id, None)
        run.pending[table_key] = table
        return (
            f"↩️ {name} 强化已取消，退还 {refund} 金，现有 {run.coins} 金。\n"
            + render_enhance(run),
            True,
        )

    return (
        "强化面板的动作用法：强化 / 强化 强化 skill|gear N / 强化 取消 skill|gear N",
        False,
    )
