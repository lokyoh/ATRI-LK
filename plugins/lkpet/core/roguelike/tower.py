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
    """为同层的 ``count`` 个战斗选项抽**互不相同**的敌人池下标。

    池子比选项少时按洗好的顺序循环补齐（配置异常兜底；正常区域 10 小怪 / 5 精英
    远多于三选一的战斗位数）。
    """
    pool = enemy_pool(region, elite=elite)
    if count <= 0 or not pool:
        return []
    indices = list(range(len(pool)))
    floor_rng(seed, floor, "elite_enemies" if elite else "enemies").shuffle(indices)
    return [indices[index % len(indices)] for index in range(count)]


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
        for key in ("strikes", "strike_mult", "crit_rate", "leech", "thorns"):
            if key in trait:
                monster[key] = trait[key]


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
    """展示名：只有**名字**，不带特点标注。

    「名称（特点）」的写法在战斗标题/胜利行里会和玩家自己的技能括号混在一起
    显得嘈杂（用户反馈：去掉战报里的（吸血）这类特点标注），因此这里只留名字；
    特点另行以 ``特点：吸血`` 的独立一行展示（见 :func:`_monster_trait_line`）。
    """
    return str(monster["name"])


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
    """按区域公式生成敌人；``elite`` 生成精英，``boss_index`` 生成候选首领。

    ``pool_index`` / ``elite_index`` 指定敌人在 ``monsters`` / ``elites`` 池里的下标
    （普通层三选一按选项预先抽好、互不相同）；省略时仍按 rng 随机抽取。
    """
    scale = floor - int(region["start"])
    base = region["monster"]["base"]
    growth = region["monster"]["growth"]
    rewards = region.get("rewards", {})
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
            elite_skills = [str(s) for s in (chosen.get("skills") or [])]
            elite_phase = chosen.get("phase")
            if elite_phase:
                elite_phases = [str(elite_phase)]
    monster = {
        "name": name,
        "hp": int(base["hp"] + growth["hp"] * scale),
        "atk": int(base["atk"] + growth["atk"] * scale),
        "def": int(base["def"] + growth["def"] * scale),
        "spd": int(base["spd"] + growth["spd"] * scale),
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
    if boss_index is None:
        _apply_monster_traits(monster, cfg)
    if is_elite:
        elite_cfg = cfg.get("elite", {}) or {}
        monster["elite"] = True
        stats_mult = float(elite_cfg.get("stats_mult", 1.6))
        reward_mult = elite_cfg.get("reward_mult", {}) or {}
        for key in ("hp", "atk", "def", "spd"):
            monster[key] = max(1, round(monster[key] * stats_mult))
        monster["coins"] = round(monster["coins"] * float(reward_mult.get("coins", 1)))
        monster["exp"] = round(monster["exp"] * float(reward_mult.get("exp", 1)))
    if boss_index is not None:
        candidates = (cfg.get("bosses") or {}).get(region.get("id", ""), [])
        if 0 <= boss_index < len(candidates):
            boss = candidates[boss_index]
            monster["boss"] = True
            monster["name"] = boss["name"]
            monster["desc"] = boss.get("desc", "")
            monster["skills"] = [str(s) for s in (boss.get("skills") or [])]
            monster["phases"] = [str(p) for p in (boss.get("phases") or [])]
            monster["hp"] = int(monster["hp"] * boss.get("hp_mult", 1))
            monster["atk"] = int(monster["atk"] * boss.get("atk_mult", 1))
            monster["def"] = int(monster["def"] * boss.get("def_mult", 1))
            monster["spd"] = int(monster["spd"] * boss.get("spd_mult", 1))
            coin_range = boss.get("coins", [monster["coins"], monster["coins"]])
            monster["coins"] = rng.randint(int(coin_range[0]), int(coin_range[1]))
            monster["exp"] = int(boss.get("exp", monster["exp"]))
    return monster
