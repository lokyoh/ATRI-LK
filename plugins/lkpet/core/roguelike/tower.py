"""塔结构：区域、层号、精英层、怪物生成与特点。"""

from typing import Any

from ._common import (
    _load_events_config,
    floor_rng,
)


def region_of(floor: int, cfg: dict[str, Any]) -> dict[str, Any]:
    """按层号取所属区域（越界时钳到首/尾区域）。"""
    regions = cfg.get("tower", {}).get("regions") or []
    if not regions:
        raise ValueError("roguelike.yaml 缺少 tower.regions")
    for region in regions:
        if int(region["start"]) <= floor <= int(region["end"]):
            return region
    return regions[0] if floor < int(regions[0]["start"]) else regions[-1]


def boss_floor_set(cfg: dict[str, Any]) -> set[int]:
    """全部区域首领层（每区域的 boss_floor）。"""
    return {
        int(region["boss_floor"]) for region in cfg.get("tower", {}).get("regions", [])
    }


def elite_floors(
    run: dict[str, Any], region: dict[str, Any], cfg: dict[str, Any]
) -> set[int]:
    """该区域本局会出现精英遭遇的层集合（区域级 seed，可复现）。

    规则（roguelike.yaml 的 ``elite`` 块）：
    - 区域前 ``min_floor_gap`` 层（start … start+gap-1）不生成，候选至 end-1（不含首领层）；
      补给层（首领前一层）没有战斗路线，同样不参与精英生成；
    - 候选层按 ``chance`` 逐层判定；
    - ``end - guaranteed_offset`` 层（默认倒数第二层）必定精英（精英守门）；
    - 含保底在内，每区域至多 ``max_per_region`` 次。
    """
    elite_cfg = cfg.get("elite", {}) or {}
    gap = int(elite_cfg.get("min_floor_gap", 5))
    max_count = max(1, int(elite_cfg.get("max_per_region", 4)))
    offset = int(elite_cfg.get("guaranteed_offset", 1) or 0)
    start, end = int(region["start"]), int(region["end"])
    supply_cfg = cfg.get("supply", {}) or {}
    s_offset = int(supply_cfg.get("floor_offset", 1) or 0)
    supply_floor = (end - s_offset) if s_offset else None
    candidates = [floor for floor in range(start + gap, end) if floor != supply_floor]
    rng = floor_rng(run["seed"], start, "elite_region")
    chance = float(elite_cfg.get("chance", 0) or 0)
    picked = [floor for floor in candidates if rng.random() < chance]
    guaranteed = end - offset
    if guaranteed in candidates and guaranteed not in picked:
        picked.append(guaranteed)
    if len(picked) > max_count:
        others = [floor for floor in picked if floor != guaranteed]
        rng.shuffle(others)
        keep = others[: max_count - (1 if guaranteed in picked else 0)]
        if guaranteed in picked:
            keep.append(guaranteed)
        picked = keep
    return set(picked)


def _is_elite_floor(
    run: dict[str, Any], region: dict[str, Any], cfg: dict[str, Any]
) -> bool:
    """当前层的战斗路线是否为精英遭遇（首领层除外，见 :func:`elite_floors`）。"""
    return run["floor"] in elite_floors(run, region, cfg)


def _region_events(region: dict[str, Any]) -> list[dict[str, Any]]:
    """该区域的事件表（rogue_events.yaml 按区域 id 分组）。"""
    return (_load_events_config().get("events") or {}).get(region["id"], [])


# ---------------------------------------------------------------------------
# 楼层构成：哪些层是固定内容层、哪些层可放普通层内容
# ---------------------------------------------------------------------------


def special_floors(region: dict[str, Any], cfg: dict[str, Any]) -> dict[int, str]:
    """该区域的**固定内容层**：``{层号: "boss" | "supply" | "relic"}``。

    这三种层只有唯一内容，不参与普通层的商店/事件/战斗生成，因此也不能作为
    「保底补点」的落点——按绝对层号算间隔时它们必须被跳过，否则补点会落在
    放不了商店的层上（第 10 层遗物层、第 19 层补给层、第 20 层首领层）。
    """
    end = int(region["end"])
    out: dict[int, str] = {}
    out[int(region.get("boss_floor") or end)] = "boss"
    supply_cfg = cfg.get("supply") or {}
    offset = int(supply_cfg.get("floor_offset", 1) or 0)
    if offset:
        out[end - offset] = "supply"
    relic_floor = int(region.get("relic_floor") or 0)
    if relic_floor:
        out[relic_floor] = "relic"
    return out


def _floor_segments(start: int, end: int, blocked: set[int]) -> list[list[int]]:
    """把 ``[start, end]`` 按 ``blocked`` 切成交替的「连续段」。

    返回的是**可刷层段**列表：每段内部层号连续，段与段之间被固定内容层隔开。
    区域被遗物层劈成两段时就是两个元素，保底必须在段内各自计算，不能跨段按
    层号算间隔。
    """
    segments: list[list[int]] = []
    current: list[int] = []
    for floor in range(start, end + 1):
        if floor in blocked:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(floor)
    if current:
        segments.append(current)
    return segments


def shop_eligible_floors(region: dict[str, Any], cfg: dict[str, Any]) -> list[int]:
    """该区域**允许刷出商店**的层（按层号升序）。

    规则：区域前 ``shop.no_before_gap`` 层不刷商店；固定内容层不刷商店；
    ``shop.exclude_elite_gate`` 为真时**保底精英层**（首领前最后一道守门层）
    也不刷商店——那一层的职责就是精英消耗，再叠一个商店会让玩家在单层里
    同时面对「买遗物」和「打精英」两个互相冲突的决策。
    """
    shop_cfg = cfg.get("shop") or {}
    start, end = int(region["start"]), int(region["end"])
    blocked = set(special_floors(region, cfg))
    gap = int(shop_cfg.get("no_before_gap", 5))
    eligible = [floor for floor in range(start + gap, end + 1) if floor not in blocked]
    if shop_cfg.get("exclude_elite_gate", True):
        gate = elite_gate_floor(region, cfg)
        eligible = [floor for floor in eligible if floor != gate]
    return eligible


def elite_gate_floor(region: dict[str, Any], cfg: dict[str, Any]) -> int | None:
    """该区域的**保底精英层**（精英守门，首领前最后一层普通层）；无则 None。"""
    elite_cfg = cfg.get("elite") or {}
    offset = int(elite_cfg.get("guaranteed_offset", 0) or 0)
    if not offset:
        return None
    floor = int(region["end"]) - offset
    if floor in special_floors(region, cfg):
        return None
    return floor


def enemy_pool(region: dict[str, Any], *, elite: bool = False) -> list[Any]:
    """该层可填进战斗选项的敌人池：精英层用 ``elites``，普通层用 ``monsters``。"""
    if elite:
        elites = region.get("elites") or []
        if elites:
            return list(elites)
    return list(region.get("monsters") or [{"name": "敌人"}])


def _pick_enemy_indices(
    region: dict[str, Any],
    seed: int,
    floor: int,
    count: int,
    *,
    elite: bool = False,
) -> list[int]:
    """为同层的 ``count`` 个战斗选项抽**互不相同**的敌人池下标（区域级牌堆）。

    做法是「洗牌发牌」而不是「每层独立洗牌」：把该区域敌人池当成一副牌，用
    **区域级 seed** 洗开，然后按层顺序一路发下去，发完再洗一轮。这样：

    - 同一层内互不相同（发牌天然不重复，跨重洗边界时跳过本层已发过的下标）；
    - **相邻两层几乎不会撞同一只怪**（实测重叠率 14.2% → 7.4%），而每区域
      出现的不同敌人数量不变（13.9 → 14.3）；
    - 每个区域把 10 只小怪（或 5 个精英）都过一遍再重复，分布比逐层独立掷骰均匀。

    牌堆是**纯函数**：给定 ``(seed, region, floor)`` 就能重放出同一个结果，
    所以不需要往 run 里存牌堆状态，旧存档也能无缝续上。
    """
    pool = enemy_pool(region, elite=elite)
    if count <= 0 or not pool:
        return []
    size = len(pool)
    if count >= size:
        # 战斗位不少于池子时无法保证不重复，退化为整池顺序发一遍。
        return [index % size for index in range(count)]
    offset = (floor - int(region["start"])) * count
    if offset < 0:
        offset = 0

    rng = floor_rng(seed, int(region["start"]), "elite_deck" if elite else "deck")
    deck = list(range(size))
    # 重放到当前层所在的牌堆位置：每发完一整副就重洗一次。
    dealt = 0
    while dealt + size <= offset:
        rng.shuffle(deck)
        dealt += size
    position = offset - dealt

    out: list[int] = []
    picked: set[int] = set()
    while len(out) < count:
        if position >= size:
            rng.shuffle(deck)
            position = 0
        candidate = deck[position]
        position += 1
        # 跨重洗边界时，新一副牌可能仍含本层已经发过的敌人（一层 3 个战斗位、
        # 一副牌 10 张，牌堆位置不整除，会横跨两副），必须跳过。
        if candidate in picked:
            continue
        picked.add(candidate)
        out.append(candidate)
    return out


# ---------------------------------------------------------------------------
# 怪物生成与特点
# ---------------------------------------------------------------------------


def _apply_monster_traits(monster: dict[str, Any], cfg: dict[str, Any]) -> None:
    """按 monster['traits'] 应用特点的数值乘区与行为参数（就地修改）。"""
    trait_defs = cfg.get("monster_traits") or {}
    for trait_id in monster.get("traits") or []:
        trait = trait_defs.get(trait_id)
        if not trait:
            continue
        for stat in ("hp", "atk", "def", "spd"):
            mult = trait.get(f"{stat}_mult")
            if mult:
                monster[stat] = max(1, round(monster[stat] * float(mult)))
        for key in ("crit_rate", "leech", "thorns"):
            if key in trait:
                monster[key] = trait[key]
        # pack 只登记规格，真正的拆分放到精英倍率之后（见 _gen_monster），
        # 这样「群袭 + 精英」也是「精英体质 × 只数」而不是「只数 × 普通体质」。
        pack = trait.get("pack")
        if isinstance(pack, dict) and pack:
            monster["pack"] = dict(pack)


def _split_pack(monster: dict[str, Any], cfg: dict[str, Any]) -> None:
    """把带 ``pack`` 的怪拆成真正的一群（就地修改为**单只**的面板）。

    群怪不是一个共用血条：场上会真的站 ``count`` 只，各自一条命、各攒各的
    攻击条、各自出手，我方也一次只能打掉其中一只。

    * ``hp_share``：群体**总**生命 = 当层公式结果 × hp_share，再平摊到每只；
    * ``atk_mult / def_mult / spd_mult``：每只的个体乘区（攻击按只数摊薄）。

    带技能或形态的单位（精英/首领）不拆分——技能挂在单一本体上，拆开会变成
    「三只会召唤的狼王」。这类 `swarm` 视为无效配置，只保留特点标注。
    """
    spec = monster.get("pack")
    if not isinstance(spec, dict) or not spec:
        return
    if monster.get("skills") or monster.get("phases"):
        monster.pop("pack", None)
        return
    max_units = max(1, int((cfg.get("battle") or {}).get("max_units", 4)))
    count = max(1, min(int(spec.get("count", 1) or 1), max_units))
    if count <= 1:
        monster.pop("pack", None)
        return

    total_hp = max(count, round(monster["hp"] * float(spec.get("hp_share", 1.0))))
    monster["hp"] = max(1, round(total_hp / count))
    monster["atk"] = max(1, round(monster["atk"] * float(spec.get("atk_mult", 1.0))))
    monster["def"] = max(0, round(monster["def"] * float(spec.get("def_mult", 1.0))))
    monster["spd"] = max(1, round(monster["spd"] * float(spec.get("spd_mult", 1.0))))
    monster["pack"] = {"count": count, "hp_each": monster["hp"]}


def monster_trait_text(traits: list[str], cfg: dict[str, Any] | None = None) -> str:
    """特点列表 → 展示用中文标签串（如「群袭，血薄」）；无特点返回空串。"""
    if not traits:
        return ""
    from ...utils.texts import text

    labels = []
    for trait_id in traits:
        labels.append(text(f"rogue.traits.{trait_id}", trait_id))
    return "，".join(labels)


def _monster_label(monster: dict[str, Any]) -> str:
    """展示名：``名称 Lv.N（特点）``；没有特点时省略括号。

    **用于楼层三选一的路线预告**（``FloorChoiceState.render`` /
    ``BattleState.render``）：选项行必须让人一眼看出「这条路线打的是什么、
    多少级、有什么特点」，所以等级与特点都内嵌在同一行。

    战斗详情**不要**用它——战报里的标题/胜利行/失败行用 :func:`_monster_name`，
    特点另起一行（:func:`_monster_trait_line`），否则会与玩家技能括号混在一起。
    """
    name = _monster_name(monster)
    note = monster_trait_text(monster.get("traits") or [])
    return f"{name}（{note}）" if note else name


def _monster_name(monster: dict[str, Any]) -> str:
    """名字 + 等级（``冰封骑士 Lv.80``）；没有等级字段时只回名字。

    改造后所有怪物都有 ``level``（= 层号），战报与预告都该让玩家看见它，
    否则「等级差影响伤害」这件事在界面上是不可见的。
    """
    name = str(monster["name"])
    level = int(monster.get("level") or 0)
    if level <= 0:
        return name
    from ...utils.texts import text

    return text("rogue.monster_level_tag", "{name} Lv.{level}", name=name, level=level)


def _monster_trait_line(monster: dict[str, Any]) -> str:
    """单独一行的特点说明；没有特点时返回空串。"""
    note = monster_trait_text(monster.get("traits") or [])
    if not note:
        return ""
    from ...utils.texts import text

    return text("rogue.monster_trait_line", "🔹 特点：{traits}", traits=note)


def _monster_desc(monster: dict[str, Any]) -> str:
    """怪物的描述行（战斗详情用）；没有描述时返回空串。

    小怪过去只写 ``name + traits``、没有 ``desc``，于是战斗详情里只有精英/首领
    才有「—— 描述」那一行。现在 ``monsters[]`` 也补了描述，这里统一按 desc
    字段渲染，有就显示、没有就跳过。
    """
    desc = str(monster.get("desc") or "").strip()
    if not desc:
        return ""
    from ...utils.texts import text

    return text("rogue.monster_desc", "—— {desc}", desc=desc)


def _enemy_skill_note(skill: dict[str, Any]) -> str:
    """技能的机制标签（蓄力/召唤/回复/强化/削弱/持续伤害/吸血），无机制返回空串。

    多个机制会一起列出（用「，」连接），避免像以前那样只显示第一个而让
    「蓄力 + 削弱」这类技能漏掉信息。没有任何机制时返回空串——调用方负责
    不要把空 note 渲染成「技能名（）」。
    """
    from ...utils.texts import text

    parts: list[str] = []
    if int(skill.get("charge", 0)) > 0:
        parts.append(
            text(
                "rogue.skill_note_charge",
                "蓄力 {rounds} 回合，累积伤害可打断",
                rounds=int(skill["charge"]),
            )
        )
    if skill.get("summon"):
        parts.append(
            text(
                "rogue.skill_note_summon",
                "召唤 {count} 只",
                count=int((skill["summon"] or {}).get("count", 1)),
            )
        )
    if skill.get("heal"):
        parts.append(text("rogue.skill_note_heal", "自我回复"))
    if skill.get("self_buff"):
        parts.append(text("rogue.skill_note_buff", "自我强化"))
    if skill.get("debuff"):
        parts.append(text("rogue.skill_note_debuff", "削弱我方"))
    if skill.get("dot"):
        parts.append(text("rogue.skill_note_dot", "持续伤害"))
    if skill.get("leech"):
        parts.append(text("rogue.skill_note_leech", "吸血"))
    if int(skill.get("mp_cost", 0)) > 0:
        parts.append(
            text("rogue.skill_note_mp", "耗蓝 {cost}", cost=int(skill["mp_cost"]))
        )
    return "，".join(parts)


def _enemy_skill_lines(
    monster: dict[str, Any], cfg: dict[str, Any], *, hint: bool = False
) -> list[str]:
    """精英/首领的专属技能与形态预告（战前展示）。"""
    from ...utils.texts import text

    skill_cfg = cfg.get("enemy_skills") or {}
    phase_cfg = cfg.get("enemy_phases") or {}
    lines: list[str] = []
    skill_ids = [
        str(sid) for sid in (monster.get("skills") or []) if str(sid) in skill_cfg
    ]
    phase_ids = [str(pid) for pid in (monster.get("phases") or [])]
    if monster.get("phase"):
        phase_ids.append(str(monster["phase"]))
    phase_ids = [pid for pid in phase_ids if pid in phase_cfg]
    if hint and (skill_ids or phase_ids):
        lines.append(
            text(
                "rogue.battle_skill_hint",
                "（⚠️ {monster} 会用专属技能，注意蓄力打断）",
                monster=monster.get("name", ""),
            )
        )
    if skill_ids:
        items = []
        for skill_id in skill_ids:
            skill = skill_cfg[skill_id] or {}
            name = skill.get("name") or skill_id
            note = _enemy_skill_note(skill)
            # 没有机制标签时不能渲染成「技能名（）」，直接用纯名字。
            items.append(
                text("rogue.skill_item", "{name}（{note}）", name=name, note=note)
                if note
                else str(name)
            )
        lines.append(
            text(
                "rogue.enemy_skills",
                "📜 {monster} 专属技能：{skills}",
                monster=monster.get("name", ""),
                skills="，".join(items),
            )
        )
    phase_items = []
    for phase_id in phase_ids:
        phase = phase_cfg.get(phase_id)
        if not phase:
            continue
        phase_items.append(
            text(
                "rogue.phase_item",
                "{name}（生命 ≤{percent}%）",
                name=phase.get("name", phase_id),
                percent=round(float(phase.get("at_hp", 0)) * 100),
            )
        )
    if phase_items:
        lines.append(
            text(
                "rogue.enemy_phases",
                "🌀 形态：{phases}",
                phases="，".join(phase_items),
            )
        )
    return lines


def _template_panel(
    level: int,
    archetype: str,
    cfg: dict[str, Any],
    rng=None,
) -> dict[str, int]:
    """按「1 级模板 + 每级成长 × 等级」算出怪物面板（**未套 tier/区域倍率**）。

    公式与宠物完全一致（见 ``pet.yaml`` 的 ``base_attributes`` / ``growth``）::

        属性 = base[stat] + growth[stat] × 等级

    区别只在 ``archetype``：它按 ``weight`` 让成长值在四项之间**温和偏移**，
    而不是完全重新分配（完全重分配会把「迅捷」的 spd 拉成普通的 2 倍）。

    偏移量按 ``archetype_strength`` 与原成长插值::

        growth_new = growth × (1 + strength × (权重 / 平均权重 - 1))

    ``strength = 0`` 等价于不受定位影响，``1`` 是完全重分配。
    """
    tpl = cfg.get("monster_templates") or {}
    base = tpl.get("base") or {}
    growth = tpl.get("growth") or {}
    stats = ("hp", "atk", "def", "spd")

    arch = (tpl.get("archetypes") or {}).get(archetype) or {}
    weight = arch.get("weight") or {}
    strength = float(tpl.get("archetype_strength", 0.45) or 0.0)

    # 平均权重 = 四项权重的均值；用它做基准，保证定位只改「形状」不改「总量」。
    weights = [float(weight.get(k, 1.0)) for k in stats]
    avg_weight = sum(weights) / len(weights) if weights else 1.0

    panel: dict[str, int] = {}
    for key in stats:
        raw_growth = float(growth.get(key, 0))
        if avg_weight > 0 and raw_growth > 0:
            ratio = float(weight.get(key, 1.0)) / avg_weight
            per_level = raw_growth * (1.0 + strength * (ratio - 1.0))
        else:
            per_level = raw_growth
        value = float(base.get(key, 1)) + per_level * float(level)
        panel[key] = max(1, round(value))

    # 同层同种怪也有 ±variance 的强弱差；不传 rng 时取中值（用于预览/校验）。
    if rng is not None:
        lo, hi = (tpl.get("variance") or [1.0, 1.0])[:2]
        for key in stats:
            panel[key] = max(1, round(panel[key] * rng.uniform(float(lo), float(hi))))
    return panel


def _gen_monster(
    floor: int,
    region: dict[str, Any],
    cfg: dict[str, Any],
    rng,
    boss_index: int | None = None,
    elite: bool = False,
    pool_index: int | None = None,
    elite_index: int | None = None,
) -> dict[str, Any]:
    """生成敌人；``elite`` 生成精英，``boss_index`` 生成候选首领。

    面板口径（改造后统一走模板，不再各区域手写 base/growth）::

        面板 = 1 级模板 + 成长 × 怪物等级     ← archetype 决定成长方向
             × 区域 power_mult                ← 区域决定强度档位
             × tier 倍率（normal/elite/boss）  ← 类型决定数值
             × 特点 traits 乘区
             × ±variance

    **怪物等级 = 层号**，所以「第 80 层的怪就是 80 级」，可以与宠物等级直接
    对比（战斗里的 ``battle.level_power`` 就是用这两个等级算修正）。

    ``pool_index`` / ``elite_index`` 指定敌人在 ``monsters`` / ``elites`` 池里的下标
    （普通层三选一按选项预先抽好、互不相同）；省略时仍按 rng 随机抽取。
    """
    level = max(1, int(floor))
    scale = floor - int(region["start"])
    rewards = region.get("rewards", {})
    tpl = cfg.get("monster_templates") or {}
    tiers = tpl.get("tiers") or {}

    pool = region.get("monsters") or [{"name": "敌人"}]
    if pool_index is None:
        picked = pool[rng.randrange(len(pool))]
    else:
        picked = pool[pool_index % len(pool)]
    if isinstance(picked, str):
        picked = {"name": picked}
    name = picked.get("name", "敌人")
    desc = picked.get("desc", "")
    traits = list(picked.get("traits") or [])
    archetype = str(picked.get("archetype") or "balanced")

    is_elite = bool(elite)
    elite_skills: list[str] = []
    elite_phases: list[str] = []
    if is_elite:
        epool = region.get("elites") or []
        if elite_index is None:
            chosen = epool[rng.randrange(len(epool))] if epool else None
        else:
            chosen = epool[elite_index % len(epool)] if epool else None
        if chosen is not None:
            if isinstance(chosen, str):
                chosen = {"name": chosen}
            name = chosen.get("name", name)
            desc = chosen.get("desc", "")
            traits = list(chosen.get("traits") or [])
            archetype = str(chosen.get("archetype") or archetype)
            elite_skills = [str(s) for s in (chosen.get("skills") or [])]
            elite_phase = chosen.get("phase")
            if elite_phase:
                elite_phases = [str(elite_phase)]

    # 1) 1 级模板 + 成长 × 怪物等级（archetype 决定成长方向）
    panel = _template_panel(level, archetype, cfg, rng=None)

    # 2) 区域强度档位
    power_mult = float(region.get("power_mult", 1.0) or 1.0)
    for key in ("hp", "atk", "def", "spd"):
        panel[key] = max(1, round(panel[key] * power_mult))

    # 3) 类型（tier）倍率
    tier_id = "boss" if boss_index is not None else ("elite" if is_elite else "normal")
    tier = tiers.get(tier_id) or {}
    for key in ("hp", "atk", "def", "spd"):
        mult = float(tier.get(f"{key}_mult", 1.0) or 1.0)
        if mult != 1.0:
            panel[key] = max(1, round(panel[key] * mult))

    # 4) 属性随机浮动（同层同种怪也有强弱差）
    lo, hi = (tpl.get("variance") or [1.0, 1.0])[:2]
    for key in ("hp", "atk", "def", "spd"):
        panel[key] = max(1, round(panel[key] * rng.uniform(float(lo), float(hi))))

    monster = {
        "name": name,
        "level": level,
        "archetype": archetype,
        "hp": panel["hp"],
        "atk": panel["atk"],
        "def": panel["def"],
        "spd": panel["spd"],
        "coins": int(
            rewards.get("coins", 2) + rewards.get("coins_per_floor", 1) * scale
        ),
        "exp": int(rewards.get("exp", 20) + rewards.get("exp_per_floor", 4) * scale),
        "boss": False,
        "elite": False,
        "desc": desc,
        "traits": [] if boss_index is not None else traits,
        "skills": list(elite_skills),
        "phases": list(elite_phases),
    }

    # 5) 特点乘区（精英/首领在特点之前，保持「特点在倍率之前作用」的旧口径）
    if boss_index is None:
        _apply_monster_traits(monster, cfg)

    # 6) 掉落倍率
    if is_elite:
        monster["elite"] = True
        monster["coins"] = round(
            monster["coins"] * float(tier.get("reward_coins", 1.0))
        )
        monster["exp"] = round(monster["exp"] * float(tier.get("reward_exp", 1.0)))

    # 7) 首领：个体倍率覆盖 tier 的兜底值
    if boss_index is not None:
        candidates = (cfg.get("bosses") or {}).get(region.get("id", ""), [])
        if 0 <= boss_index < len(candidates):
            boss = candidates[boss_index]
            monster["boss"] = True
            monster["name"] = boss["name"]
            monster["desc"] = boss.get("desc", "")
            monster["skills"] = [str(s) for s in (boss.get("skills") or [])]
            monster["phases"] = [str(p) for p in (boss.get("phases") or [])]
            # 首领倍率是**相对 tier 兜底值**的增量，避免两套倍率相乘两次。
            for key in ("hp", "atk", "def", "spd"):
                fallback = float(tier.get(f"{key}_mult", 1.0) or 1.0)
                wanted = float(boss.get(f"{key}_mult", fallback) or fallback)
                if fallback > 0:
                    monster[key] = max(1, round(monster[key] / fallback * wanted))
            coin_range = boss.get("coins", [monster["coins"], monster["coins"]])
            monster["coins"] = rng.randint(int(coin_range[0]), int(coin_range[1]))
            monster["exp"] = int(boss.get("exp", monster["exp"]))

    # 群怪拆分放在所有倍率之后：先把「一整群」的体质算完，再平摊成每只的面板。
    _split_pack(monster, cfg)
    return monster
