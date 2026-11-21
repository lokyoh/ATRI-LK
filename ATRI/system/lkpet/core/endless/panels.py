"""无尽模式的面板：状态 / 装备 / 技能 / 遗物。

面板**只能在非战斗状态打开**：战斗中每条指令都要推进战斗，插进面板会让
玩家以为还有别的操作。

每个面板都实现自己能做的操作，操作走 ``宠物.无尽 <面板> <动作> <序号>``：
面板自己解析，不进楼层状态机。
"""

from __future__ import annotations

from typing import Any

from .describe import attr_label, attr_line, attr_value, describe_attrs
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
    skill_power,
    skill_specs,
)

SLOT_CN = {"weapon": "武器", "armor": "防具", "accessory": "饰品"}
"""槽位中文名。"""

PANELS = ("状态", "装备", "技能", "遗物")
"""可打开的面板名。"""

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
        f"生命 {run.hp}/{run.hp_max} · 魔力 {run.mp}/{run.mp_max}",
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
    """装备面板：已穿的四件 + 库存，编号供「穿上 / 卸下 / 分解」使用。"""
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
            lines.append("  " + gear_line(spec))

    # 库存编号只服务「穿上 N / 分解 N」，所以从 1 起；与已穿的分开计数
    stock = stock_of(run)
    if stock:
        lines.append("库存（穿上 N / 分解 N）：")
        for index, gear_id in enumerate(stock, 1):
            spec = specs.get(str(gear_id)) or {}
            lines.append(
                f"  {index}. {gear_line(spec)}（分解 {decompose_value(spec)} 金）"
            )
    else:
        lines.append("库存：空")
    return "\n".join(lines)


def gear_actions(run: Any) -> list[tuple[str, str]]:
    """装备面板的按钮：按当前库存与槽位给出可执行的动作。"""
    from ...utils.texts import command_start

    base = command_start() + "宠物.无尽"
    out: list[tuple[str, str]] = [(base + " 装备", "看装备")]
    stock = stock_of(run)
    for index in range(1, len(stock) + 1):
        out.append((base + f" 装备 穿上 {index}", f"穿上 {index}"))
    for slot in ("weapon", "armor", "accessory"):
        if (run.equip or {}).get(slot):
            out.append((base + f" 装备 卸下 {SLOT_CN[slot]}", f"卸下{SLOT_CN[slot]}"))
    for index in range(1, len(stock) + 1):
        out.append((base + f" 装备 分解 {index}", f"分解 {index}"))
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
    """一个技能的一行：档位 + 名字（元素·系）+ 威力。"""
    elem = ELEMENT_CN.get(str(spec.get("element")), str(spec.get("element")))
    series = SERIES_CN.get(str(spec.get("series")), str(spec.get("series")))
    tier = int(spec.get("tier", 1) or 1)
    name = str(spec.get("name") or spec.get("id"))
    power = skill_power(spec, times)
    plus = f" 强化×{times}" if times else ""
    return f"T{tier} {name}（{elem}·{series}）威力 {power}{plus}"


def render_skills(run: Any) -> str:
    """技能面板：已装 4 槽 + 学会但未装的。"""
    specs = skill_specs()
    enhance = dict((run.pending or {}).get("enhance") or {})
    active = _active_ids(run)
    lines = [f"📖 技能（槽位 {len(active)}/{active_slots()}）"]
    if not active:
        lines.append("还没装技能。")
    for index, skill_id in enumerate(active, 1):
        spec = specs.get(str(skill_id)) or {}
        times = int(enhance.get(str(skill_id), 0) or 0)
        lines.append(f"  {index}. {skill_line(spec, times)}")

    from .flow import known_skills

    # 未装 = 已学会但没在槽里；全库 160 个不该出现在玩家的面板里
    bench = [s for s in known_skills(run) if s not in active and s in specs]
    if bench:
        lines.append(f"未装（{len(bench)} 个）：")
        for index, skill_id in enumerate(bench[:12], 1):
            spec = specs.get(skill_id) or {}
            times = int(enhance.get(skill_id, 0) or 0)
            lines.append(f"  {index}. {skill_line(spec, times)}")
    return "\n".join(lines)


def skill_actions(run: Any) -> list[tuple[str, str]]:
    from ...utils.texts import command_start
    from .flow import known_skills

    base = command_start() + "宠物.无尽"
    out: list[tuple[str, str]] = [(base + " 技能", "看技能")]
    active = _active_ids(run)
    bench = [s for s in known_skills(run) if s not in active]
    for index in range(1, len(bench) + 1):
        out.append((base + f" 技能 换上 {index}", f"换上 {index}"))
    for index in range(1, len(active) + 1):
        out.append((base + f" 技能 换下 {index}", f"换下 {index}"))
    return out[:8]


def skill_action(run: Any, action: str, arg: str) -> tuple[str, bool]:
    """技能面板的操作：换上 N / 换下 N。"""
    specs = skill_specs()
    active = _active_ids(run)
    if action == "换下":
        if not 1 <= int(arg) <= len(active):
            return "已装的技能没有这个编号。", False
        skill_id = active.pop(int(arg) - 1)
        run.skills = {**run.skills, "active": active, "passive": []}
        name = (specs.get(skill_id) or {}).get("name") or skill_id
        return f"✅ 已换下 {name}。\n" + render_skills(run), True

    if action == "换上":
        from .flow import known_skills

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
        return f"✅ 已换上 {name}{tail}。\n" + render_skills(run), True

    return "技能面板的动作用法：技能 / 技能 换上 N / 技能 换下 N", False


# ---------------------------------------------------------------- 遗物


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
    groups = {"散件": []}
    for relic_id, dup in owned.items():
        spec = specs.get(relic_id) or {"id": relic_id}
        groups.setdefault(str(spec.get("set") or "散件"), []).append((spec, dup))
    for key, items in groups.items():
        if not items:
            continue
        from .relics import set_meta

        label = str((set_meta().get(key) or {}).get("name") or key)
        lines.append(f"{label}：")
        for spec, dup in items:
            from .describe import relic_note

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


def relic_actions(run: Any) -> list[tuple[str, str]]:
    from ...utils.texts import command_start

    return [(command_start() + "宠物.无尽 遗物", "看遗物")]


# ---------------------------------------------------------------- 查看


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
"""转伤属性（§8.5）：防转伤 / 速转伤 / 蓝转伤 / 血转伤让非攻击构筑也有输出。"""


def _dir_note(spec: dict[str, Any]) -> str:
    """这条技能吃哪个属性；辅助与防御类不吃属性，直说。"""
    series = str(spec.get("series") or "")
    if series in ("support", "guard"):
        return "不吃属性（辅助 / 防御向，不造成伤害）"
    key = str(spec.get("dir") or "atk")
    return DIR_CN.get(key, attr_label(key))


def render_detail(kind: str, spec: dict[str, Any]) -> str:
    """「查看」的展开：一件东西的完整介绍。"""
    lines = [f"🔍 {kind}：{spec.get('name') or spec.get('id')}"]
    tier = spec.get("tier")
    if tier:
        lines.append(f"档位 T{tier}")
    if kind == "技能":
        elem = ELEMENT_CN.get(str(spec.get("element")), str(spec.get("element")))
        series = SERIES_CN.get(str(spec.get("series")), str(spec.get("series")))
        lines.append(f"{elem}·{series} · 威力 {skill_power(spec)}")
        lines.append(f"耗魔 {spec.get('mp_cost', 0)} · 冷却 {spec.get('cooldown', 0)}")
        lines.append("吃属性：" + _dir_note(spec))
        target = "群体" if str(spec.get("target")) == "all" else "单体"
        lines.append(f"目标：{target}")
    elif kind == "装备":
        slot = SLOT_CN.get(str(spec.get("slot")), str(spec.get("slot")))
        lines.append(f"槽位：{slot}")
        special = spec.get("special") or {}
        if special.get("desc"):
            lines.append(f"特殊：{special['desc']}")
        lines.extend(describe_attrs(gear_effects(spec)))
    else:
        set_key = str(spec.get("set") or "")
        lines.append("套装：" + (set_key or "散件"))
        lines.extend(describe_attrs(dict(spec.get("effect") or {})))
        set_key = str(spec.get("set") or "")
        if set_key:
            bonus = set_bonuses({str(spec.get("id")): 1})
            if bonus:
                lines.append("占 1 件时的套装加成：" + "，".join(attr_line(bonus)))
    return "\n".join(lines)


def render_panel(run: Any, name: str) -> str:
    """按名字打开一个面板。"""
    table = {
        "状态": render_status,
        "装备": render_gear,
        "技能": render_skills,
        "遗物": render_relics,
    }
    render = table.get(str(name))
    if render is None:
        return "没有这个面板，可选：" + " / ".join(PANELS)
    return render(run)


def panel_actions(run: Any, name: str) -> list[tuple[str, str]]:
    """一个面板底部的按钮：(完整指令, 显示名)。"""
    if name == "状态":
        return []
    if name == "装备":
        return gear_actions(run)
    if name == "技能":
        return skill_actions(run)
    if name == "遗物":
        return relic_actions(run)
    return []
