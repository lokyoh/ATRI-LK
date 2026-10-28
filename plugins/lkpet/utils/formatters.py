"""Display layer: turn plugin state into the messages players read.

Every template comes from ``texts.yaml`` (via :func:`utils.texts.text`); this
module only assembles the fields.  Enum keys that have no YAML counterpart
(``rarity`` / ``health_state`` / ``emotion_state``) get a small label map here.
"""

from typing import Any

from .texts import text

RARITY_LABELS = {
    "common": "普通",
    "rare": "稀有",
    "epic": "史诗",
    "legendary": "传说",
    "mythic": "神话",
}

HEALTH_LABELS = {
    "healthy": "健康",
    "tired": "疲惫",
    "sick": "生病",
    "injured": "受伤",
}

EMOTION_LABELS = {
    "happy": "开心",
    "normal": "平静",
    "sad": "难过",
    "angry": "生气",
    "excited": "兴奋",
}


def _load_config(name: str) -> dict[str, Any]:
    from ..config_loader import load_config

    return load_config(name)


def format_days(days: int) -> str:
    """Render a day count as 天 / 个月 / 年."""
    days = max(0, int(days))
    if days < 30:
        return f"{days}天"
    if days < 365:
        return f"{days // 30}个月{days % 30}天"
    return f"{days // 365}年{(days % 365) // 30}个月"


def format_pet_status(pet, pet_config: dict[str, Any] | None = None) -> str:
    """Full status card for ``/宠物.状态``."""
    from ..core.affection import get_affection_level

    cfg = pet_config if pet_config is not None else _load_config("pet")
    personality = cfg.get("personalities", {}).get(pet.personality, {})
    attributes = pet.attributes or {}

    fields = {
        "name": pet.name,
        "personality": personality.get("name", pet.personality),
        "rarity": RARITY_LABELS.get(pet.rarity, pet.rarity),
        "level": pet.level,
        "exp": pet.exp,
        "exp_next": pet.exp_next,
        "age": format_days(pet.age_days),
        "lifespan": format_days(pet.lifespan_days),
        "health": HEALTH_LABELS.get(pet.health_state, pet.health_state),
        "emotion": EMOTION_LABELS.get(pet.emotion_state, pet.emotion_state),
        "hunger": pet.hunger,
        "mood": pet.mood,
        "fatigue": pet.fatigue,
        "cleanliness": pet.cleanliness,
        "affection": pet.affection,
        "affection_level": get_affection_level(pet.affection),
        "action_points": pet.action_points,
        "hp": attributes.get("hp", 0),
        "atk": attributes.get("atk", 0),
        "def": attributes.get("def", 0),
        "spd": attributes.get("spd", 0),
        "mp": attributes.get("mp", 0),
    }
    title = text("status.title", "🐾 {name} 的状态", name=pet.name)
    body = text("status.body", "", **fields)
    return f"{title}\n{body}" if body else title


def format_daily_report(settle: dict[str, Any]) -> str:
    """Render the outcome of ``core.daily.check_daily_reset``."""
    if not settle.get("need_settle"):
        return text("daily.no_settle", "今天已经结算过了")

    report = settle.get("report") or []
    to_day = report[-1]["day"] if report else settle.get("days_settled", 0)
    lines = [
        text(
            "daily.title",
            "📅 已结算 {days_settled} 天",
            days_settled=settle.get("days_settled", 0),
            to_day=to_day,
        )
    ]
    if settle.get("report_truncated"):
        lines.append(text("daily.truncated", "", kept=len(report)))
    lines.extend(entry.get("log", "") for entry in report)
    lines.append(
        text(
            "daily.action_points",
            "行动点已重置为 {action_points}",
            action_points=settle.get("action_points", 0),
        )
    )

    reincarnation = settle.get("reincarnation")
    if reincarnation and reincarnation.get("can_reincarnate"):
        lines.append(
            text(
                "daily.reincarnation",
                "",
                reasons="、".join(reincarnation.get("reasons", [])),
                points=reincarnation.get("estimated_points", 0),
            )
        )
    return "\n".join(line for line in lines if line)


PASSIVE_EFFECT_LABELS = {
    "atk": "攻",
    "def": "防",
    "spd": "速",
    "mp": "魔",
    "max_hp": "生命上限",
    "crit_rate": "暴击率",
    "dmg_mult": "输出",
}


def _passive_effect_note(effect: dict[str, Any]) -> str:
    """被动技能的效果短描述，如「攻+6」「暴击率+15%」。"""
    parts = []
    for key, value in effect.items():
        if not isinstance(value, int | float):
            continue
        label = PASSIVE_EFFECT_LABELS.get(key, key)
        if key in ("crit_rate", "dmg_mult"):
            parts.append(f"{label}+{round(value * 100)}%")
        else:
            parts.append(f"{label}+{int(value)}")
    return "，".join(parts)


def _mp_cost_note(spec: dict[str, Any]) -> str:
    """主动技能的耗魔展示值：``mp_cost`` 为 0（或缺失）时标成「无」。

    直接显示 ``0`` 会让人以为是「不耗魔但也没写」的脏数据，写「无」更清楚。
    """
    try:
        cost = int(spec.get("mp_cost", 0) or 0)
    except (TypeError, ValueError):
        cost = 0
    return str(cost) if cost > 0 else "无"


AOE_TARGETS = frozenset({"all", "all_enemies", "aoe"})
"""``target`` 取这些值即为群体技能（与 ``battle.BattleSession._is_aoe`` 同口径）。"""


def _skill_scope_note(spec: dict[str, Any]) -> str:
    """技能作用范围的展示后缀：群体技能标「〔群〕」，单体不加任何字符。

    只给群体加标记，避免把每一行都塞满「单体」这种废话；玩家扫一眼就能
    认出哪几个是清群怪用的。
    """
    if str(spec.get("target", "single")).strip().lower() in AOE_TARGETS:
        return "〔群〕"
    return ""


def format_skill_list(pet, skills_config: dict[str, Any] | None = None) -> str:
    """Render the skill card: equipped slots first, then the collection pool.

    装备中的技能带威力 / **耗魔** / 熟练度；未装备的只列名字，并提示如何装备。
    """
    from ..core.skills import (
        SOURCE_LABELS,
        equipped_limit,
        equipped_skills,
        known_skills,
        skill_name,
        skill_specs,
        source_of,
    )

    cfg = skills_config if skills_config is not None else _load_config("skills")
    specs = skill_specs(cfg)
    equipped = equipped_skills(pet)
    collection = known_skills(pet)

    lines = [
        text(
            "skill.title",
            "📚 {name} 的技能",
            name=pet.name,
            active_used=len(equipped["active"]),
            active_max=equipped_limit("active", cfg),
            passive_used=len(equipped["passive"]),
            passive_max=equipped_limit("passive", cfg),
            known=len(collection["active"]) + len(collection["passive"]),
        )
    ]

    for slot in ("active", "passive"):
        label = "主动" if slot == "active" else "被动"
        lines.append(
            text(
                f"skill.section.{slot}",
                "【{label}】装备 {used}/{max}",
                label=label,
                used=len(equipped[slot]),
                max=equipped_limit(slot, cfg),
            )
        )
        if not equipped[slot]:
            lines.append(text("skill.equip_empty", "· （空槽）"))
            continue
        for skill_id in equipped[slot]:
            spec = specs.get(skill_id) or {}
            if slot == "active":
                lines.append(
                    text(
                        "skill.equipped_line",
                        "· {name}{scope}｜威力 {power}｜耗魔 {mp_cost}｜熟练度 {proficiency}",
                        name=skill_name(skill_id, cfg),
                        scope=_skill_scope_note(spec),
                        power=spec.get("power", 0),
                        mp_cost=_mp_cost_note(spec),
                        proficiency=round(
                            (getattr(pet, "skill_proficiency", None) or {}).get(
                                skill_id, 0.0
                            ),
                            2,
                        ),
                        effect="",
                    )
                )
            else:
                lines.append(
                    text(
                        "skill.equipped_passive_line",
                        "· {name}（{effect}）",
                        name=skill_name(skill_id, cfg),
                        effect=_passive_effect_note(spec.get("effect") or {})
                        or "无加成",
                        power=0,
                        proficiency=0.0,
                    )
                )

    unused = [
        skill_id
        for slot in ("active", "passive")
        for skill_id in collection[slot]
        if skill_id not in equipped[slot]
    ]
    if unused:
        lines.append(
            text(
                "skill.bench",
                "🎒 技能库（未装备 {count} 个）：{names}",
                count=len(unused),
                names="、".join(
                    f"{skill_name(skill_id, cfg)}[{SOURCE_LABELS.get(source_of(pet, skill_id, cfg), '常规')}]"
                    for skill_id in unused
                ),
            )
        )
    if not any(collection[slot] for slot in ("active", "passive")):
        lines.append(text("skill.empty", "还没有学会任何技能"))
    lines.append(
        text(
            "skill.usage",
            "用法：/宠物.技能 装备 <技能名>｜/宠物.技能 卸下 <技能名>",
        )
    )
    return "\n".join(line for line in lines if line)


def format_talent_list(pet, talents_config: dict[str, Any] | None = None) -> str:
    """List the pet's allocated talents."""
    cfg = talents_config if talents_config is not None else _load_config("talents")
    talents = cfg.get("talents", {})

    lines = [
        text(
            "talent.title",
            "✨ {name} 的天赋",
            name=pet.name,
            points=pet.talent_points,
        )
    ]
    entries = []
    for talent_id, level in (pet.talents or {}).items():
        talent = talents.get(talent_id)
        if talent is None:
            continue
        entries.append(
            text(
                "talent.line",
                "",
                name=talent.get("name", talent_id),
                level=level,
                max_level=talent.get("max_level", 0),
            )
        )
    lines.extend(entries or [text("talent.empty", "还没有分配任何天赋")])
    return "\n".join(line for line in lines if line)


def format_biography(bio, name: str = "宠物", limit: int = 10) -> str:
    """Show the newest ``limit`` biography entries plus unlocked titles."""
    entries = list(bio.entries or [])[-max(1, limit) :]
    lines = [
        text("bio.title", "📖 {name} 的传记", name=name, count=len(bio.entries or []))
    ]
    if bio.titles_unlocked:
        lines.append(text("bio.titles", "", titles="、".join(bio.titles_unlocked)))
    if not entries:
        lines.append(text("bio.empty", "传记还是空的"))
    for entry in entries:
        lines.append(
            text(
                "bio.line",
                "",
                type=entry.get("type", ""),
                content=entry.get("content") or entry.get("title", ""),
            )
        )
    return "\n".join(line for line in lines if line)


def format_daily_logs(bio, limit: int = 10) -> str:
    """Show the newest ``limit`` daily logs."""
    logs = list(bio.daily_logs or [])[-max(1, limit) :]
    lines = [text("logs.title", "🗓 最近的每日日志")]
    if not logs:
        lines.append(text("logs.empty", "还没有日志"))
    for entry in logs:
        lines.append(
            text(
                "logs.line",
                "",
                day=entry.get("day", 0),
                content=entry.get("content", ""),
            )
        )
    return "\n".join(line for line in lines if line)


def format_reincarnation_check(
    check: dict[str, Any],
    reinc=None,
    *,
    pet=None,
) -> str:
    """Render ``/轮回`` — condition report only, never a performed cycle."""
    cycle = (reinc.total_cycles + 1) if reinc is not None else 1
    lines = [text("reincarnation.title", "🔄 轮回", cycle=cycle)]
    if not check.get("can_reincarnate"):
        lines.append(text("reincarnation.unavailable", "当前无法轮回"))
        return "\n".join(line for line in lines if line)

    lines.append(
        text(
            "reincarnation.available",
            "",
            reasons="、".join(check.get("reasons", [])),
            estimated_points=check.get("estimated_points", 0),
            achievement_points=getattr(reinc, "achievement_points", 0),
            spent_points=getattr(reinc, "spent_points", 0),
        )
    )
    lines.append(text("reincarnation.manual_hint", ""))
    return "\n".join(line for line in lines if line)


def format_reincarnation_result(result: dict[str, Any]) -> str:
    """Render the outcome of a confirmed reincarnation.

    ``result`` 里的 ``drawn_skills`` / ``kept_skills`` 由
    ``core.reincarnation.perform_reincarnation`` 给出：满级轮回抽到的轮回技能
    （数量由 ``grants.reincarnation_draw`` 决定），以及跨世保留下来的技能
    （轮回技能 + 肉鸽奖励技能）。``drawn_skill`` 是旧版单抽字段，仍兼容。
    """
    from ..core.skills import skill_name

    lines = [
        text(
            "reincarnation.done",
            "",
            **{
                key: value
                for key, value in result.items()
                if key
                not in (
                    "drawn_skill",
                    "drawn_skills",
                    "kept_skills",
                    "max_level_cycle",
                )
            },
        )
    ]

    drawn = [item for item in (result.get("drawn_skills") or []) if item]
    if not drawn and result.get("drawn_skill"):
        drawn = [result["drawn_skill"]]
    if result.get("max_level_cycle"):
        if not drawn:
            lines.append(text("reincarnation.drawn_skill_none", ""))
        else:
            names = "、".join(
                str(item.get("name") or item.get("id", "")) for item in drawn
            )
            if len(drawn) == 1:
                line = text(
                    "reincarnation.drawn_skill",
                    "🌟 满级轮回，从轮回技能池抽得：{name}",
                    name=names,
                )
            else:
                line = text(
                    "reincarnation.drawn_skills",
                    "🌟 满级轮回，从轮回技能池抽得：{names}",
                    names=names,
                )
            if any(not item.get("equipped") for item in drawn):
                line += text(
                    "reincarnation.skill_bench",
                    "（技能槽已满，请在「/宠物.技能」中装备）",
                )
            lines.append(line)

    drawn_ids = {str(item.get("id", "")) for item in drawn}
    kept = result.get("kept_skills") or {}
    names = [
        skill_name(skill_id)
        for slot in ("active", "passive")
        for skill_id in kept.get(slot, [])
        if str(skill_id) not in drawn_ids
    ]
    if names:
        lines.append(
            text(
                "reincarnation.kept_skills",
                "🎒 保留技能（轮回技能 / 奖励技能）：{names}",
                names="、".join(names),
            )
        )
    elif not drawn:
        lines.append(text("reincarnation.no_kept_skills", ""))
    return "\n".join(line for line in lines if line)


def format_perk_list(
    reincarnation_config: dict[str, Any],
    available: int,
) -> str:
    """List the perks exchangeable with achievement points.

    旧版特权列表（``reincarnation.yaml`` 的 ``perks`` 块）。商店上线后
    ``/宠物.特权`` 已改走 :func:`format_shop_overview`，这里保留只为兼容。
    """
    perks = reincarnation_config.get("perks", {})
    lines = [text("perk.title", "🏅 成就点特权", available=available)]
    if not perks:
        lines.append(text("perk.empty", "暂无可兑换特权"))
    for perk_id, perk in perks.items():
        lines.append(
            text(
                "perk.line",
                "",
                perk_id=perk_id,
                name=perk.get("name", perk_id),
                cost=perk.get("cost", 0),
            )
        )
    return "\n".join(line for line in lines if line)


# ---------------------------------------------------------------------------
# 轮回商店
# ---------------------------------------------------------------------------

SHOP_EFFECT_LABELS = {
    "exp_bonus": "经验",
    "atk_bonus": "攻击",
    "def_bonus": "防御",
    "hp_bonus": "生命",
    "spd_bonus": "速度",
    "mp_bonus": "魔力",
    "start_level_bonus": "起始等级",
    "exp_pct": "经验",
    "atk_pct": "攻击",
    "def_pct": "防御",
    "hp_pct": "生命",
    "spd_pct": "速度",
    "mp_pct": "魔力",
}
"""商店效果键的中文名（展示层专用，不进 texts.yaml）。"""


def format_shop_effect(effect: dict[str, float] | None) -> str:
    """把效果 dict 渲染成「攻击 +1%｜生命 +1%」或「起始等级 +1 级」。

    百分比键（``*_pct``）按 ``0.01 = +1%`` 展示；平值键展示为整数；
    ``start_level_bonus`` 单独标注单位。空效果返回「无」。
    """
    if not effect:
        return text("shop.effect_none", "无")
    parts = []
    for key, value in effect.items():
        label = SHOP_EFFECT_LABELS.get(key, key)
        if key.endswith("_pct"):
            parts.append(f"{label} +{round(float(value) * 100, 2):g}%")
        elif "start_level" in key:
            parts.append(f"{label} +{int(value)} 级")
        else:
            parts.append(f"{label} +{int(value)}")
    return "｜".join(parts)


def _shop_level_text(info: dict[str, Any]) -> str:
    """「2/20」或「已买 3 级」（无限等级没有分母）。"""
    cap = info.get("cap")
    if cap:
        return text(
            "shop.item_level_max",
            "{owned}/{cap}",
            owned=info.get("owned", 0),
            cap=cap,
        )
    return text("shop.item_level_inf", "已买 {owned} 级", owned=info.get("owned", 0))


def format_shop_overview(reinc) -> str:
    """``/宠物.商店`` —— 可用点数 + 分类列表。"""
    from ..core import shop

    cfg = _load_config("reincarnation")
    specs = shop.catalog(cfg)
    if not specs:
        return (
            text("shop.overview_title", "🏛 轮回商店", available=0)
            + "\n"
            + text("shop.empty", "商店里还没有任何商品。")
        )

    available = shop.available_points(reinc)
    lines = [
        text(
            "shop.overview_title",
            "🏛 轮回商店｜可用 {available} 点",
            available=available,
            total=int(getattr(reinc, "achievement_points", 0) or 0),
            spent=int(getattr(reinc, "spent_points", 0) or 0),
        )
    ]
    for cat in shop.categories(cfg):
        order = int(cat.get("order", 999))
        members = [
            spec for spec in specs.values() if str(spec.get("category")) == cat["id"]
        ]
        if not members:
            continue
        infinite = any(shop.level_mode(s) == shop.LEVEL_INFINITE for s in members)
        mode = text(
            "shop.overview_mode_infinite" if infinite else "shop.overview_mode_capped",
            "无限成长" if infinite else "有限等级",
        )
        lines.append(
            text(
                "shop.overview_line",
                "{order}. {name}",
                order=order,
                name=str(cat.get("name") or cat.get("id")),
                count=len(members),
                mode=mode,
            )
        )

    lines.append(text("shop.overview_hint", "发送「/宠物.商店 1」查看分类。"))

    respec_cfg = (cfg.get("shop") or {}).get("respec") or {}
    tickets = int(getattr(reinc, "respec_tickets", 0) or 0)
    if respec_cfg:
        lines.append(
            text(
                "shop.overview_respec",
                "♻️ 洗点券 {tickets} 张",
                tickets=tickets,
                free=int(respec_cfg.get("free_per_cycle", 0) or 0),
            )
        )
    return "\n".join(line for line in lines if line)


def format_shop_category(reinc, category_id: str) -> str:
    """``/宠物.商店 1`` —— 某分类下的商品行。"""
    from ..core import shop

    cfg = _load_config("reincarnation")
    cat = next(
        (c for c in shop.categories(cfg) if str(c.get("id")) == str(category_id)),
        None,
    )
    if cat is None:
        raise ValueError("没有这个分类")

    order = int(cat.get("order", 999))
    members = [
        spec
        for spec in shop.catalog(cfg).values()
        if str(spec.get("category")) == cat["id"]
    ]
    lines = [
        text(
            "shop.category_title",
            "🏛 {name}",
            name=str(cat.get("name") or cat.get("id")),
            desc=str(cat.get("desc") or ""),
        )
    ]
    if not members:
        lines.append(text("shop.category_empty", "这个分类还没有商品。"))
        return "\n".join(lines)

    for spec in members:
        info = shop.describe_item(reinc, spec, cfg)
        if not info["unlocked"]:
            price_text = text(
                "shop.item_locked",
                "（未开放）",
                cycles=(spec.get("unlock") or {}).get("cycles", 1),
            )
        elif info["price"] is None:
            price_text = text("shop.item_maxed", "（已满级）")
        else:
            price_text = text("shop.item_price", "{price} 点", price=info["price"])
            if not info["affordable"]:
                price_text += text(
                    "shop.item_gap", "（还差 {gap} 点）", gap=info["gap"]
                )
        lines.append(
            text(
                "shop.item_line",
                "· {number} {name}",
                number=info["number"],
                name=info["name"],
                level=_shop_level_text(info),
                price=price_text,
                gap="",
            )
        )

    lines.append(
        text(
            "shop.category_hint",
            "发送「/宠物.商店 {number}」查看详情。",
            order=order,
            number=f"{order}-1",
        )
    )
    return "\n".join(line for line in lines if line)


def format_shop_item(reinc, item_id: str) -> str:
    """``/宠物.商店 1-3`` —— 单件详情。"""
    from ..core import shop

    cfg = _load_config("reincarnation")
    spec = shop.catalog(cfg).get(str(item_id))
    if spec is None:
        raise ValueError("商品不存在")
    info = shop.describe_item(reinc, spec, cfg)

    cat = next(
        (c for c in shop.categories(cfg) if str(c.get("id")) == info["category"]),
        {},
    )
    lines = [
        text(
            "shop.detail_title",
            "🏛 {number} {name}",
            category=str(cat.get("name") or ""),
            number=info["number"],
            name=info["name"],
        ),
        text("shop.detail_level", "当前：{level}", level=_shop_level_text(info)),
        text(
            "shop.detail_effect",
            "效果：{effect}",
            effect=format_shop_effect(info["per_level"]) + "/级",
        ),
    ]
    if info["desc"]:
        lines.append(f"「{info['desc']}」")

    if not info["unlocked"]:
        cycles = (spec.get("unlock") or {}).get("cycles", 1)
        lines.append(
            text(
                "shop.detail_locked", "需要累计轮回 {cycles} 次才开放。", cycles=cycles
            )
        )
    elif info["price"] is None:
        lines.append(text("shop.detail_maxed", "这条已经买满了。"))
        lines.append(
            text(
                "shop.detail_next",
                "累计：{next}",
                next=format_shop_effect(info["current_effect"]),
            )
        )
    else:
        lines.append(
            text(
                "shop.detail_next",
                "下一级：{next}",
                next=format_shop_effect(info["next_effect"]),
            )
        )
        lines.append(
            text(
                "shop.detail_price",
                "价格：{price} 点（可用 {available}）",
                price=info["price"],
                available=info["available"],
            )
        )
        if info["affordable"]:
            lines.append(
                text(
                    "shop.detail_confirm",
                    "发送「/宠物.商店 确认 {number}」购买。",
                    number=info["number"],
                )
            )
        else:
            lines.append(
                text(
                    "shop.detail_short",
                    "还差 {gap} 点。多轮回几次再来吧。",
                    gap=info["gap"],
                )
            )
    return "\n".join(line for line in lines if line)


def format_shop_purchase(result: dict[str, Any]) -> str:
    """购买成交回执。"""
    if result.get("consumable"):
        return text(
            "shop.bought_respec",
            "✅ 已购买「{name}」（-{price} 点，剩余 {remaining}）。现有洗点券 {tickets} 张。",
            **result,
        )
    lines = [
        text(
            "shop.bought",
            "✅ 已购买「{name}」第 {level} 级（-{price} 点，剩余 {remaining}）",
            **result,
        ),
        text(
            "shop.bought_effect",
            "效果：{before} → {after}",
            before=format_shop_effect(result.get("before")),
            after=format_shop_effect(result.get("after")),
        ),
    ]
    if result.get("consumable_on_reincarnation"):
        lines.append(
            text(
                "shop.bought_consumed",
                "★ 这份起始等级会在下一次轮回时生效，用掉即清零。",
            )
        )
    lines.append(text("shop.bought_hint", "发送「/宠物.商店」继续浏览。"))
    return "\n".join(line for line in lines if line)


def format_shop_respec(preview: dict[str, Any], *, done: bool = False) -> str:
    """洗点预览 / 完成回执。"""
    if done:
        return text(
            "shop.respec_done",
            "♻️ 洗点完成：退还 {refund} 点，清空 {items} 条共 {levels} 级（剩余券 {tickets} 张）。",
            **preview,
        )
    lines = [
        text("shop.respec_title", "♻️ 洗点预览"),
        text(
            "shop.respec_body",
            "将退还 {refund} 点，清空 {items} 条共 {levels} 级。",
            **preview,
        ),
        text(
            "shop.respec_tickets",
            "现有洗点券 {tickets} 张。",
            tickets=preview.get("tickets", 0),
        ),
        text("shop.respec_confirm", "发送「/宠物.商店 洗点 确认」执行。"),
    ]
    return "\n".join(line for line in lines if line)
