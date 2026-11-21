"""无尽模式 §11.10 怪物抽取 + §11.2 生物特点抗性。

沿用爬塔的怪池（每区 30 小怪 / 7 精英 / 3 首领），不新做一批怪。
生物特点抗性**由描述推出**，不是随机：熔岩里的怪永远火抗，冰壳的永远冰抗。
"""

from __future__ import annotations

import random
from typing import Any

#: 描述关键词 → 抗性。顺序重要：先匹配更具体的。
RESIST_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        (
            "熔岩",
            "岩浆",
            "烧红",
            "熔渣",
            "火甲",
            "烈焰",
            "焦黑",
            "烧焦",
            "火星",
            "火尾",
            "火苗",
            "焰",
            "灼",
        ),
        "fire",
    ),
    (("冰", "霜", "雪", "冻", "寒", "冰锥"), "ice"),
    (("雷", "电", "带电", "闪电", "麻"), "thunder"),
    (("风", "掠过", "气旋", "白影"), "wind"),
    (("泥", "土", "石", "岩", "苔藓", "树", "根", "泥浆", "石臼"), "earth"),
    (("光", "圣", "星", "曜"), "light"),
    (("暗", "影", "幽", "虚空", "夜", "腐"), "dark"),
)

#: 描述里表示「钝击穿不透」的词 → 物理抗性（§11.2 的泥浆团例子）
PHYS_RES_WORDS = ("泥浆", "半透明", "浓得", "黏")

#: 描述里表示「看不清就砍不中」的词 → 闪避而非抗性
DODGE_WORDS = ("看不清", "雾", "显形", "钻", "一戳就破")


def load_rogue_config() -> dict[str, Any]:
    from ...config_loader import load_config

    return load_config("roguelike")


def region_of_id(
    region_id: str, cfg: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """按区域 id 取爬塔的区域条目（含 30 只小怪 / 7 精英）。"""
    cfg = cfg or load_rogue_config()
    for item in (cfg.get("tower") or {}).get("regions") or []:
        if str(item.get("id")) == str(region_id):
            return item
    return None


def monsters_of(
    region_id: str, cfg: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    node = region_of_id(region_id, cfg)
    return list((node or {}).get("monsters") or [])


def elites_of(
    region_id: str, cfg: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    node = region_of_id(region_id, cfg)
    return list((node or {}).get("elites") or [])


def bosses_of(
    region_id: str, cfg: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    cfg = cfg or load_rogue_config()
    return list((cfg.get("bosses") or {}).get(str(region_id)) or [])


def draw_monster(
    rng: random.Random,
    region_id: str,
    *,
    elite: bool = False,
    boss: bool = False,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """按层类型从对应池独立抽一只；**不做去重**（§11.10）。"""
    pool = (
        bosses_of(region_id, cfg)
        if boss
        else (elites_of(region_id, cfg) if elite else monsters_of(region_id, cfg))
    )
    if not pool:
        return None
    return dict(rng.choice(pool))


def resist_of(
    desc: str, region_id: str = "", rng: random.Random | None = None
) -> dict[str, Any]:
    """抗性与闪避：**优先由描述推出**，推不出才用区域主打元素兜底。

    §11.2 的两条要求同时成立：
    1. 「数值必须由特点推出」——描述指向什么就给什么（泥浆团给物理抗性）；
    2. 「每区域以 3 种元素为主」——描述没指向时按该区主打元素取一个，
       否则深渊这类用隐喻写描述的区会一只抗性怪都没有。

    ``rng`` 为空时不做兜底（纯描述推），方便单独测描述规则。
    """
    from .floor import REGION_ELEMENTS

    text = str(desc or "")
    out: dict[str, Any] = {}
    if any(word in text for word in PHYS_RES_WORDS):
        out["resist"] = {"normal": 30.0}
    else:
        for words, elem in RESIST_RULES:
            if any(word in text for word in words):
                out["resist"] = {elem: 30.0}
                break
    if any(word in text for word in DODGE_WORDS):
        out["dodge"] = 0.2
    if not out.get("resist") and rng is not None:
        elements = REGION_ELEMENTS.get(str(region_id)) or ()
        if elements:
            out["resist"] = {str(rng.choice(list(elements))): 30.0}
    return out
