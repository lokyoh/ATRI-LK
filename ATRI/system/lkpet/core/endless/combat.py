"""无尽模式的元素克制与属性乘区。

战斗引擎（battle.py）本身不认识元素，这里把「谁打谁、哪个元素、吃哪些加成」
算成一个乘区字典交给它。**不传这个字典时，引擎行为与改动前完全一致**，
所以爬塔路径不受影响。

克制环（7 元素，物理不参与）：

    火 -> 冰 -> 风 -> 土 -> 雷 -> 暗 -> 光 -> 火

倍率：克 +25% / 被克 -20%。
"""

from __future__ import annotations

from typing import Any

#: 克制环。每元素克下一个，被上一个克。
ELEMENT_RING = ("fire", "ice", "wind", "earth", "thunder", "dark", "light")

ELEMENT_CN = {
    "normal": "物理",
    "fire": "火",
    "ice": "冰",
    "wind": "风",
    "earth": "土",
    "thunder": "雷",
    "dark": "暗",
    "light": "光",
}

COUNTER_BONUS = 1.25
"""克制时的倍率。"""

COUNTER_PENALTY = 0.80
"""被克时的倍率。"""


def _ring_index(element: str) -> int | None:
    try:
        return ELEMENT_RING.index(str(element))
    except ValueError:
        return None


def counter_multiplier(attacker: str, defender: str) -> float:
    """攻击元素打防守元素时的克制倍率。

    物理（normal）不参与环：对所有元素都是 1.0，也不会被任何元素克制。
    两端都在环上时才判克制。
    """
    left = _ring_index(attacker)
    right = _ring_index(defender)
    if left is None or right is None:
        return 1.0
    size = len(ELEMENT_RING)
    if (left + 1) % size == right:
        return COUNTER_BONUS
    if (right + 1) % size == left:
        return COUNTER_PENALTY
    return 1.0


def elemental_scale(element: str, bonus: dict[str, float]) -> float:
    """元素加伤与全元素加伤的乘区。

    键名沿用数据集的写法：``elem_<元素>`` 与 ``elem_all``（后者不含物理）。
    物理加成走 ``phys_dmg``，由 :func:``damage_scale`` 处理。
    """
    elem = str(element or "normal")
    if elem == "normal":
        return 1.0
    total = 1.0 + float(bonus.get("elem_" + elem, 0.0) or 0.0)
    total *= 1.0 + float(bonus.get("elem_all", 0.0) or 0.0)
    return total


def player_element(attrs: dict[str, float]) -> str:
    """玩家这一局的主攻元素：取 elem_<元素> 加成最高的那个。

    全部无加伤时返回 normal（物理）。这只决定克制环吃不吃加成，
    不影响伤害的基础数值。
    """
    best = "normal"
    best_value = 0.0
    for elem in ELEMENT_RING:
        value = float(attrs.get("elem_" + elem, 0.0) or 0.0)
        if value > best_value:
            best_value = value
            best = elem
    return best


def damage_scale(element: str, attrs: dict[str, float]) -> float:
    """普攻/技能加伤 × 元素加伤 × 物理加伤，一次算完。"""
    elem = str(element or "normal")
    phys = 1.0 + float(attrs.get("phys_dmg", 0.0) or 0.0) if elem == "normal" else 1.0
    return phys * elemental_scale(elem, attrs)


def resistance_scale(element: str, resist: dict[str, float]) -> float:
    """元素抗性的减伤系数：``n / (n + R)``。

    用减伤式而不是加算：加算在 100% 处会归零（无敌），减伤式永远到不了 0。
    ``n`` 与 ``def_softness`` 同口径（100）。
    """
    n = 100.0
    elem = str(element or "normal")
    value = float(resist.get(elem, 0.0) or 0.0)
    if elem != "normal":
        value = max(value, float(resist.get("all", 0.0) or 0.0))
    return n / (n + max(0.0, value))


def mods_builder(attrs: dict[str, Any]) -> Any:
    """返回 ``(element, enemy_element, kind) -> mods`` 的构造器。

    ``battle`` 每次出手都会调它：元素加伤、克制、``skill_dmg`` 都必须按
    这一击的技能算，而外层只挂得了一份默认值。
    """

    def build(
        *, element: str = "normal", enemy_element: str = "normal", kind: str = "attack"
    ) -> dict[str, float]:
        return build_pet_mods(
            element=element,
            enemy_element=enemy_element,
            kind=kind,
            attrs=attrs,
        )

    return build


def build_pet_mods(
    *,
    element: str = "normal",
    enemy_element: str = "normal",
    kind: str = "attack",
    attrs: dict[str, Any] | None = None,
) -> dict[str, float]:
    """我方一次出手要用的乘区（喂给 battle 的 ``pet_combat_mods``）。"""
    a = attrs or {}
    damage_key = "skill_dmg" if kind == "skill" else "basic_dmg"
    return {
        # 敌方抗性要按**这一击的元素**查表，所以元素必须留在乘区里
        "element": str(element or "normal"),
        "armor_pen": float(a.get("armor_pen", 0.0) or 0.0),
        "damage_bonus": 1.0 + float(a.get(damage_key, 0.0) or 0.0),
        "element_bonus": damage_scale(element, a),
        "counter": counter_multiplier(element, enemy_element),
        "accuracy": float(a.get("accuracy", 0.0) or 0.0),
    }
