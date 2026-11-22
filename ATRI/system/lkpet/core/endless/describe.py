"""无尽模式的属性 / 技能 / 装备 / 遗物文案。

一局里玩家会同时看到 27 个属性键、160 个技能、150 件装备、316 件遗物。
只给数字玩家看不懂「坚固 20%」在做什么，所以每个键都要有中文名 + 一句说明。
"""

from __future__ import annotations

from typing import Any

ATTR_LABELS: dict[str, str] = {
    "atk": "攻击",
    "def": "防御",
    "spd": "速度",
    "hp": "生命",
    "mp": "魔力",
    "atk_pct": "攻击",
    "def_pct": "防御",
    "hp_pct": "生命",
    "mp_pct": "魔力",
    "crit_rate": "暴击率",
    "crit_mult": "暴击伤害",
    "armor_pen": "穿甲",
    "solid": "坚固",
    "basic_dmg": "普攻伤害",
    "skill_dmg": "技能伤害",
    "phys_dmg": "物理伤害",
    "phys_res": "物理抗性",
    "elem_all": "全元素伤害",
    "elem_fire": "火伤",
    "elem_ice": "冰伤",
    "elem_thunder": "雷伤",
    "elem_wind": "风伤",
    "elem_earth": "土伤",
    "elem_light": "光伤",
    "elem_dark": "暗伤",
    "dodge": "闪避",
    "accuracy": "命中",
    "lifesteal": "吸血",
    "shield_gain": "护盾获取",
    "heal_gain": "治疗获取",
    "debuff_res": "负面抗性",
    "element_resist": "元素抗性",
}
"""属性键 → 中文名（平值用不加 % 的写法，百分比另行换算）。"""

ATTR_DESCS: dict[str, str] = {
    "atk": "伤害分子，越高打得越疼",
    "def": "减伤分母，100 点约减半",
    "spd": "攻击条积攒速度，越高出手越频繁",
    "hp": "局内生命上限",
    "mp": "局内魔力上限，技能消耗它",
    "atk_pct": "攻击力按百分比再增",
    "def_pct": "防御按百分比再增",
    "hp_pct": "生命上限按百分比再增",
    "mp_pct": "魔力上限按百分比再增",
    "crit_rate": "每次命中判定暴击的概率",
    "crit_mult": "在基准 1.8 倍之上再加，基准本身不可减",
    "armor_pen": "按比例无视对方防御",
    "solid": "抵消对方的穿甲，穿不透就按原防御算",
    "basic_dmg": "只加成普攻",
    "skill_dmg": "只加成技能",
    "phys_dmg": "只加成物理（无属性）伤害",
    "phys_res": "对物理伤害的减伤，按 n/(n+抗性) 折算",
    "elem_all": "七种元素统一加伤，不含物理",
    "elem_fire": "火属性技能加伤",
    "elem_ice": "冰属性技能加伤",
    "elem_thunder": "雷属性技能加伤",
    "elem_wind": "风属性技能加伤",
    "elem_earth": "土属性技能加伤",
    "elem_light": "光属性技能加伤",
    "elem_dark": "暗属性技能加伤",
    "dodge": "有概率完全避开这一击",
    "accuracy": "抵掉对方的闪避",
    "lifesteal": "造成伤害后按比例回血",
    "shield_gain": "获得护盾时按比例放大",
    "heal_gain": "治疗量按比例放大",
    "debuff_res": "缩短负面效果的持续回合",
    "element_resist": "对特定元素的减伤，按 n/(n+抗性) 折算",
}
"""属性键 → 一句说明（面板里点「查看」时给出）。"""

RESIST_PREFIX = "elem_resist"
"""元素抗性在数据里是 dict（``element_resist: {fire: 30}``），不是平铺键。"""


def _pct(value: float) -> str:
    pct = round(float(value) * 100, 1)
    body = f"{pct:g}"
    return f"+{body}%" if pct >= 0 else f"{body}%"


def attr_label(key: str) -> str:
    """属性键的中文名；未知键原样返回，不至于渲染成空白。"""
    if str(key) == "element_resist":
        return "元素抗性"
    return ATTR_LABELS.get(str(key), str(key))


PCT_KEYS = frozenset(
    {
        "crit_rate",
        "crit_mult",
        "armor_pen",
        "solid",
        "basic_dmg",
        "skill_dmg",
        "phys_dmg",
        "elem_all",
        "elem_fire",
        "elem_ice",
        "elem_thunder",
        "elem_wind",
        "elem_earth",
        "elem_light",
        "elem_dark",
        "atk_pct",
        "def_pct",
        "hp_pct",
        "mp_pct",
        "dodge",
        "accuracy",
        "lifesteal",
        "shield_gain",
        "heal_gain",
        "debuff_res",
    }
)
"""按**比例**写进数据的键：0.1 要显示成 +10%。"""

ELEMENT_NAMES = {
    "normal": "物理",
    "fire": "火",
    "ice": "冰",
    "thunder": "雷",
    "wind": "风",
    "earth": "土",
    "light": "光",
    "dark": "暗",
}
"""元素抗性 dict 的键 → 中文名。"""


def attr_value(key: str, value: Any) -> str:
    """把一个属性值写成玩家看得懂的形式：百分比键带 %，其余带整数。"""
    if isinstance(value, dict):
        # 元素抗性是 {元素: 数值}，键要翻成中文元素名而不是原样输出
        return "、".join(
            f"{ELEMENT_NAMES.get(str(k), str(k))}{round(float(v), 1)}"
            for k, v in value.items()
        )
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float) and not value.is_integer():
        if str(key).endswith("_pct") or str(key) in PCT_KEYS:
            return _pct(value)
        return f"{value:g}"
    number = int(value)
    if str(key).endswith("_pct") or str(key) in PCT_KEYS:
        return _pct(number)
    return str(number)


def attr_line(attrs: dict[str, Any], keys: tuple[str, ...] | None = None) -> list[str]:
    """把属性字典排成「名字 值」的多行文本（一行一个，不挤成一长条）。"""
    rows: list[str] = []
    for key in keys or tuple(attrs):
        if key not in attrs:
            continue
        value = attrs[key]
        if isinstance(value, int | float) and not value:
            continue
        rows.append(f"{attr_label(key)} {attr_value(key, value)}")
    return rows


def describe_attrs(attrs: dict[str, Any]) -> list[str]:
    """「名字 值 —— 说明」，用于「查看」的详细展开。"""
    rows: list[str] = []
    for key in tuple(attrs):
        value = attrs[key]
        if isinstance(value, int | float) and not value:
            continue
        note = ATTR_DESCS.get(str(key), "")
        head = f"{attr_label(key)} {attr_value(key, value)}"
        rows.append(f"{head} —— {note}" if note else head)
    return rows


DIR_LABELS = {
    "atk": "攻击",
    "def": "防御",
    "spd": "速度",
    "mp": "魔力",
    "hp": "生命",
}
"""转伤属性 → 中文名（技能说明用）。"""


def relic_note(spec: dict[str, Any]) -> str:
    """遗物的一行说明：属性 + 套装归属。"""
    note = "，".join(attr_line(dict(spec.get("effect") or {})))
    set_key = str(spec.get("set") or "")
    if set_key and note:
        from .relics import set_meta

        name = str((set_meta().get(set_key) or {}).get("name") or set_key)
        return f"{note}（{name} 套）"
    return note or "无加成"


def skill_note(spec: dict[str, Any]) -> str:
    """技能的一行说明：吃哪个属性 + 威力 / 耗魔。"""
    series = str(spec.get("series") or "")
    elem = ELEMENT_NAMES.get(str(spec.get("element")), str(spec.get("element")))
    if series in ("support", "guard"):
        head = f"{elem}·辅助"
    else:
        key = str(spec.get("dir") or "atk")
        head = f"{elem}·{DIR_LABELS.get(key, key)}转伤"
    power = int(spec.get("power", 0) or 0)
    cost = int(spec.get("mp_cost", 0) or 0)
    tail = f"威力 {power}" if power else "不造成伤害"
    return f"{head}，{tail}，耗魔 {cost}"


def gear_note(spec: dict[str, Any]) -> str:
    """装备的一行说明：主副词条 + 特殊效果。"""
    parts = attr_line({**dict(spec.get("main") or {}), **dict(spec.get("sub") or {})})
    special = spec.get("special") or {}
    if special.get("desc"):
        parts.append(str(special["desc"]))
    return "，".join(parts) or "无加成"
