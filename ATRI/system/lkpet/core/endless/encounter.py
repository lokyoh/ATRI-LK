"""无尽模式的战斗接入：把 BattleSession 包成「一步一指令」。

会话存在 ``run.pending['session']`` 里，每次玩家回一条指令就推进一帧，
停在下一个「轮到我方」或战斗结束时返回。

**不碰养成数据**：用的是 ``EndlessAvatar`` 只读代理。
"""

from __future__ import annotations

import random
from math import floor
from typing import Any

from ..roguelike.battle import BattleSession
from .attrs import apply_to_session, merge_attrs
from .avatar import EndlessAvatar
from .combat import mods_builder, player_element, resistance_scale
from .flow import combat_attrs
from .run import RogueRun
from .session import dump_session, load_session
from .skills import skill_specs
from .telemetry import telemetry

ACTION_ATTACK = {"kind": "attack"}
ACTION_MEDITATE = {"kind": "meditate"}
ACTION_DEFEND = {"kind": "defend"}


def _avatar(run: RogueRun) -> EndlessAvatar:
    from .skills import load_skills_config

    attrs = combat_attrs(run)
    base = {
        "hp": int(attrs.get("hp", 40)),
        "atk": int(attrs.get("atk", 5)),
        "def": int(attrs.get("def", 0)),
        "spd": int(attrs.get("spd", 10)),
        "mp": int(attrs.get("mp", 10)),
    }
    return EndlessAvatar(
        base,
        level=int(run.progression.level),
        skills={
            "active": [str(s) for s in (run.skills.get("active") or [])],
            "passive": [],
        },
        proficiency=dict(run.pending.get("proficiency") or {}),
        # 无尽技能 id（sk_<元素>_<系列>_<阶>）与爬塔的 skills.yaml 完全不重叠，
        # 不传这张表就永远解析不到，技能槽等于空的。
        skills_config=load_skills_config(),
        enhance=dict(run.pending.get("enhance") or {}),
    )


AFFIX_PER_FLOORS = 15
"""词缀数：每 15 层 +1。"""

MAX_AFFIX = 6
"""词缀数上限（§11.6）。"""


def affix_stats(kinds: list[str], rng: random.Random) -> dict[str, Any]:
    """把词缀名翻成战斗引擎认识的单位字段。

    数值按词缀条数与层数浮动，但只给引擎已有的字段——
    引擎不认识的东西不硬塞（否则又是一批「看起来实现了」的假接口）。
    """
    out: dict[str, Any] = {}
    for kind in kinds:
        if kind == "solid":
            out["solid"] = round(rng.uniform(0.05, 0.25), 3)
        elif kind == "dodge":
            out["dodge"] = round(rng.uniform(0.03, 0.15), 3)
        elif kind == "crit_rate":
            out["crit_rate"] = round(rng.uniform(0.02, 0.10), 3)
        elif kind == "rage":
            out["rage"] = 1
            out["rage_per_stack"] = round(rng.uniform(0.02, 0.06), 3)
        elif kind == "elem_resist":
            elem = rng.choice(list(ENEMY_TIMEOUT_ELEMENTS))
            out.setdefault("resist", {})[elem] = round(rng.uniform(10, 40), 1)
        elif kind == "death_burst":
            out["death_burst"] = round(rng.uniform(0.3, 0.8), 3)
        elif kind == "charge":
            # 给一个带 charge 的敌方技能；引擎自己会走「蓄力→可打断」那条路
            skill = rng.choice(list(CHARGE_SKILLS))
            out.setdefault("skills", []).append(skill)
        elif kind == "summon":
            skill = rng.choice(list(SUMMON_SKILLS))
            out.setdefault("skills", []).append(skill)
    return out


CHARGE_SKILLS = ("e_charge_horn", "e_quake", "e_meteor", "e_annihilate")
"""§11.7 蓄力大招：这些敌方技能带 ``charge``，可被打断。"""

SUMMON_SKILLS = ("e_summon_spider", "e_summon_bat", "e_summon_wolf", "e_summon_shade")
"""§11.7 召唤：这些敌方技能会召来援军。"""

AFFIX_KINDS = (
    ("elem_resist", "等离子抗性"),
    ("solid", "坚固"),
    ("dodge", "闪避"),
    ("crit_rate", "暴击"),
    ("rage", "受击叠怒"),
    # 规则型（§11.7）：给的是技能与机制，不是面板
    ("charge", "蓄力大招"),
    ("death_burst", "死亡自爆"),
    ("summon", "召唤"),
)

PACK_OPTION_COUNT = 3
"""层选项「群怪」的只数：3 只各自独立抽的怪。"""

MAX_PACK = 20
"""敌方单位上限（含群怪与召唤物）；实际只数取自怪池的 swarm 特点。"""


MECHANIC_KEYS = (
    """特点里直接铺到单位上的机制字段（引擎已支持）""",
    "crit_rate",
    "leech",
    "thorns",
    "dodge",
    "regen_pct",
    "death_burst",
)


def apply_traits(
    drawn: dict[str, Any] | None,
    cfg: dict[str, Any],
    stats: dict[str, float],
) -> dict[str, Any]:
    """把怪池条目的 traits 全量展开：数值乘区 + 机制字段（爬塔同款规则）。"""
    trait_defs = cfg.get("monster_traits") or {}
    out: dict[str, Any] = {}
    for trait_id in (drawn or {}).get("traits") or []:
        trait = trait_defs.get(str(trait_id)) or {}
        for stat in ("hp", "atk", "def", "spd"):
            mult = float(trait.get(stat + "_mult", 0.0) or 0.0)
            if mult:
                stats[stat] = max(1, floor(float(stats[stat]) * mult))
        for key in MECHANIC_KEYS:
            if key in trait:
                out[key] = trait[key]
    return out


def pack_of(drawn: dict[str, Any] | None, cfg: dict[str, Any]) -> dict[str, Any]:
    """从怪池条目的 traits 里取 swarm 的 pack 规格；没有就返回单体。"""
    swarm = (cfg.get("monster_traits") or {}).get("swarm") or {}
    spec = dict(swarm.get("pack") or {})
    for trait_id in (drawn or {}).get("traits") or []:
        if str(trait_id) == "swarm":
            return spec
    return {}


ENEMY_PEN_BASE = 0.05
"""敌方穿甲起点。"""

ENEMY_PEN_STEP = 0.003
"""每层增长的穿甲量；0.003 × 100 层 = 0.30，封顶 0.35。"""

ENEMY_TIMEOUT_ELEMENTS = (
    "normal",
    "fire",
    "ice",
    "wind",
    "earth",
    "thunder",
    "dark",
    "light",
)
"""超时扣血随机取的元素池：玩家只堆单一抗性挡不住。"""

SHIELD_BASE_PCT = 0.20
"""护盾基数：入场按生命上限的这个比例折算，再乘 (1 + shield_gain)。"""

ELITE_FIGHT_MULT = 1.6
"""强化战斗怪物的额外强度系数。"""

NORMAL_CHANCE = 0.05
"""普通战斗的遗物掉率（12 号平衡）。"""

ELITE_FIGHT_CHANCE = 0.10
"""强化战斗的遗物掉率。"""

INNER_BASE = 0.50
"""阶段内缩放的起点：第 1 层 = 0.50 + 0.05 = 0.55。"""

INNER_STEP = 0.05
"""阶段内每层递增；第 10 层 = 1.00（正好整份）。"""

GROWTH_BASE = 0.15
"""第 1 个大阶段的阶段倍率增量。"""

GROWTH_MAJOR_STEP = 0.05
"""每个大阶段结束后 growth 再 +0.05。"""

ENDLESS_WEAKEN = 0.5
"""无尽削弱系数 K：HP / ATK / DEF 三项乘它。"""


def _build_monster(
    run: RogueRun,
    cfg: dict[str, Any],
    rng: random.Random,
    *,
    elite_fight: bool = False,
    pack_option: bool = False,
) -> dict[str, Any]:
    """按层数与类型造一只对手。数值走 P4 的公尺（刀数反推）。"""
    from .config import enemy_scale_cfg, template_attributes
    from .floor import MAJOR_STAGES, is_boss_floor, is_elite_floor, offset_of, stage_of

    stage = stage_of(run.floor)
    offset = offset_of(run.floor)
    cfg_balance = enemy_scale_cfg(cfg)

    # 阶段内缩放：第 1 层 0.55 起、每层 +0.05，第 10 层正好 1.00
    inner = INNER_BASE + INNER_STEP * offset
    # 阶段倍率：累加各小阶段的 growth；growth 每大阶段 +0.05
    growth_base = float(
        (cfg.get("enemy") or {}).get("growth_per_stage", GROWTH_BASE) or GROWTH_BASE
    )
    scale = 1.0
    for past in range(stage - 1):
        scale += growth_base + GROWTH_MAJOR_STEP * (past // MAJOR_STAGES)

    tier_mult = (
        1.6 if is_elite_floor(run.floor) else (2.4 if is_boss_floor(run.floor) else 1.0)
    )
    if "elite" in cfg_balance and is_elite_floor(run.floor):
        tier_mult = float(cfg_balance.get("elite", tier_mult))
    if "boss" in cfg_balance and is_boss_floor(run.floor):
        tier_mult = float(cfg_balance.get("boss", tier_mult))
    # 强化战斗（事件/选项池里的 elite_fight）单独乘一档：它不是精英层，
    # 但「更强 + 更多奖励」是设计 11.8 / F7 明确的规则。
    if elite_fight:
        tier_mult *= ELITE_FIGHT_MULT

    # 按**模板**缩放，不按玩家当前面板：否则玩家练得越强敌人跟着变强，练级等于没练。
    base = template_attributes(cfg)

    def _mult(key: str, fallback: float) -> float:
        return float(cfg_balance.get(key, fallback))

    k = float(cfg_balance.get("k", ENDLESS_WEAKEN))
    hp = max(1, floor(float(base.get("hp", 40)) * k * scale * inner * tier_mult))
    atk = max(1, floor(float(base.get("atk", 5)) * k * scale * inner * tier_mult))
    def_ = max(0, floor(float(base.get("def", 0)) * k * scale * inner * tier_mult))
    # SPD 不吃 k / 阶段倍率 / 阶段内缩放：只受分级系数影响
    spd = max(1, floor(float(base.get("spd", 10)) * tier_mult))
    # 敌人元素取本阶段的区域主推元素（§11.2）：每区 3 种，
    # 单一元素套装在该区域至少有一半有效。
    from .floor import REGION_ELEMENTS

    elements = REGION_ELEMENTS.get(str(run.region)) or ("normal",)
    element = str(rng.choice(list(elements)))

    from .monsters import draw_monster, resist_of

    # §11.10：从本区域对应的池独立抽一只；抽不到才退回模板名字
    drawn = draw_monster(
        rng,
        str(run.region),
        elite=is_elite_floor(run.floor),
        boss=is_boss_floor(run.floor),
    )
    # 层选项「群怪」：3 只**各自独立抽**的怪，不是一个本体的复制。
    extra: list[dict[str, Any]] = []
    if pack_option:
        base = drawn or {}
        extra = [
            draw_monster(
                random.Random(f"{run.seed}:{run.floor}:pack:{i}"),
                str(run.region),
                elite=is_elite_floor(run.floor),
                boss=is_boss_floor(run.floor),
            )
            for i in range(1, PACK_OPTION_COUNT)
        ]
        extra = [e for e in extra if e and e.get("name") != base.get("name")]
    # §11.4 第二层：精英/首领的 preset 决定技能与相位，必须展开
    preset = {}
    if drawn and drawn.get("preset"):
        preset = dict(
            (cfg.get("monster_presets") or {}).get(str(drawn["preset"])) or {}
        )

    # §11.2：抗性由**描述**推出，不是随机
    traits = (
        resist_of(str(drawn.get("desc") or ""), str(run.region), rng) if drawn else {}
    )
    # 怪池自带的 traits 全量继承：数值乘区算在这里（早于精英/首领倍率之外的放大），
    # 机制字段（棘刺/自爆/再生/闪避/吸血/暴击）直接铺到单位上。
    stats = {"hp": hp, "atk": atk, "def": def_, "spd": spd}
    traits.update(apply_traits(drawn, cfg, stats))
    hp, atk, def_, spd = (
        int(stats["hp"]),
        int(stats["atk"]),
        int(stats["def"]),
        int(stats["spd"]),
    )

    # 群怪只数由怪池自带的 swarm 特点决定（爬塔同款 monster_traits.pack），
    # 不再随层数增长：带 swarm 的才是 3 只，其余一律单体。
    pack_spec = pack_of(drawn, cfg)
    pack_count = max(1, min(MAX_PACK, int(pack_spec.get("count", 1) or 1)))
    if pack_option:
        pack_count = PACK_OPTION_COUNT
        pack_spec = {}
    if pack_count > 1:
        share = float(pack_spec.get("hp_share", 1.0) or 1.0)
        atk = max(1, floor(atk * float(pack_spec.get("atk_mult", 1.0) or 1.0)))
        def_ = max(0, floor(def_ * float(pack_spec.get("def_mult", 1.0) or 1.0)))
        spd = max(1, floor(spd * float(pack_spec.get("spd_mult", 1.0) or 1.0)))
        # 群体总生命 = 公式生命 × hp_share，再平摊到每只（总血与只数无关）
        hp = max(1, floor(hp * share / pack_count))
    affix_count = min(MAX_AFFIX, (run.floor - 1) // AFFIX_PER_FLOORS)
    # 精英/首领至少带上设计规定的下限（§11.8：精英 3–4 / 首领 5–6）
    if is_boss_floor(run.floor):
        affix_count = max(5, affix_count)
    elif is_elite_floor(run.floor):
        affix_count = max(3, affix_count)
    affixes = [kind for kind, _ in rng.sample(list(AFFIX_KINDS), affix_count)]

    # 层选项「群怪」：3 只各自独立抽的怪，每只按自己的特点单独算面板。
    members: list[dict[str, Any]] = []
    if pack_option:
        specs = [drawn or {}, *extra][:PACK_OPTION_COUNT]
        for index, spec_drawn in enumerate(specs, 1):
            member_rng = random.Random(f"{run.seed}:{run.floor}:member:{index}")
            m_stats = {"hp": hp, "atk": atk, "def": def_, "spd": spd}
            m_traits = apply_traits(spec_drawn, cfg, m_stats)
            members.append(
                {
                    "name": str(spec_drawn.get("name") or f"敌人{index}"),
                    "desc": str(spec_drawn.get("desc") or ""),
                    "hp": int(m_stats["hp"]),
                    "atk": int(m_stats["atk"]),
                    "def": int(m_stats["def"]),
                    "spd": int(m_stats["spd"]),
                    "level": int(getattr(run.progression, "level", 1) or 1),
                    "element": element,
                    "armor_pen": min(
                        0.35, ENEMY_PEN_BASE + ENEMY_PEN_STEP * (run.floor - 1)
                    ),
                    "skills": list(spec_drawn.get("skills") or []),
                    "preset": str(spec_drawn.get("preset") or ""),
                    "passive": str(spec_drawn.get("passive") or ""),
                    "traits": [str(t) for t in (spec_drawn.get("traits") or [])],
                    **m_traits,
                    **affix_stats(affixes, member_rng),
                }
            )
    # 异质群怪表头统一写「群怪」，逐只名字由各自单位给出
    if pack_option and members:
        name = "群怪"

    name = str((drawn or {}).get("name") or "") or (
        "首领"
        if is_boss_floor(run.floor)
        else ("精英" if is_elite_floor(run.floor) else "野生怪物")
    )
    return {
        "name": name,
        "hp": hp,
        "atk": atk,
        "def": def_,
        "spd": spd,
        # **必须显式给等级**：引擎在 monster 没有 level 时会退回 _state_floor(state)，
        # 也就是拿「层号」当怪物等级。无尽模式的层号能到 300，而玩家等级只有 50 上下，
        # 于是输出乘区被钳到 0.5、承伤乘区被钳到 1.9——两个极端同时生效。
        # 这里让怪物等级跟随玩家等级，等级乘区恒为 (1.0, 1.0)，
        # 真正决定难度的是模板数值 × 阶段 × inner × K。
        "level": int(getattr(run.progression, "level", 1) or 1),
        "boss": is_boss_floor(run.floor),
        "elite": is_elite_floor(run.floor),
        "element": element,
        # 敌方穿甲：按层数缓慢成长、封顶 0.35。玩家「坚固」抵消它，
        # 这是 solid 唯一的消费点。
        "armor_pen": min(0.35, ENEMY_PEN_BASE + ENEMY_PEN_STEP * (run.floor - 1)),
        "kind": "elite_fight"
        if elite_fight
        else (
            "boss"
            if is_boss_floor(run.floor)
            else ("elite" if is_elite_floor(run.floor) else "normal")
        ),
        # 怪池自带的技能 / 精英 preset / 专属 passive（§11.4）
        "skills": list((drawn or {}).get("skills") or preset.get("skills") or []),
        "preset": str((drawn or {}).get("preset") or ""),
        "passive": str((drawn or {}).get("passive") or ""),
        # 特点 id 留着给战报的「🔹 特点：…」一行用
        "traits": [str(t) for t in (drawn or {}).get("traits") or []],
        "phases": list(
            (drawn or {}).get("phases")
            or ([preset["phase"]] if preset.get("phase") else [])
        ),
        # 群怪（§11.11）：swarm 特点定只数；层选项「群怪」则是 3 只不同的怪。
        "pack": {"count": pack_count if not members else len(members)},
        "members": members,
        # 词缀（§11.7）：每 15 层 +1 条，上限 6；同类不叠加。
        "affixes": affixes,
        **traits,
        **affix_stats(affixes, rng),
    }


def begin_battle(
    run: RogueRun,
    cfg: dict[str, Any] | None = None,
    *,
    elite_fight: bool = False,
    pack_option: bool = False,
) -> dict[str, Any]:
    """开一场战斗：构造 session 并跑到第一个「轮到我方」。

    返回 ``{"ended": bool, "victory": bool, "log": [...]}``，会话本身存进 run.pending。
    """
    from .config import load_endless_config

    cfg = cfg or load_endless_config()
    rng = random.Random(f"{run.seed}:{run.floor}:battle")
    avatar = _avatar(run)
    monster = _build_monster(
        run, cfg, rng, elite_fight=elite_fight, pack_option=pack_option
    )
    # 魔力只在战斗内存续；run.mp 只表示**下一场战斗的起始魔力**（默认满）。
    # 事件里的 run_mp 因此仍然有效：它调整的是下一场开场的蓝量。
    mp_cap = run.mp_max
    start_mp = max(0, min(mp_cap, int(run.mp)))
    # 消耗即复位：事件削减的是「下一场」的起始蓝，不该永久生效
    run.mp = mp_cap
    state = {
        "hp": run.hp,
        "hp_max": run.hp_max,
        "mp": start_mp,
        "mp_max": mp_cap,
        "buffs": {"atk": 0, "def": 0, "spd": 0},
        "relics": [],
    }
    # 进战斗就清掉上一层留下的 type/options，否则按钮会一直指向开局抽卡
    run.pending.pop("type", None)
    run.pending.pop("options", None)
    run.pending["_hp_before"] = run.hp
    run.pending["_acts"] = {}
    # 每步一份新列表：只收这一步产生的战报，不把整场都叠回去
    log: list[tuple[int, str]] = []
    session = BattleSession(avatar, state, monster, cfg, rng, log=log)
    session.manual = True
    _attach_attrs(session, run, monster)
    session.step_until_my_turn()
    return _after_step(run, session, monster, cfg, count_step=False)


def _attach_attrs(
    session: BattleSession, run: RogueRun, monster: dict[str, Any]
) -> None:
    """把遗物/装备/套装的局内属性挂到会话上。

    ``begin_battle`` 与 ``submit`` **都必须调**：``load_session`` 只恢复
    血/蓝/条/冷却，不恢复元素加伤、穿甲、闪避、抗性、治疗/护盾加成，
    漏一处就从第二条指令起全部归零。
    """
    from .relics import active_specials

    attrs = combat_attrs(run)
    apply_to_session(session, merge_attrs(attrs, _resist_layer(attrs)))
    # 事件遗物的触发式特殊效果：每场都重新挂（会话会被重建）
    session.pet_specials = active_specials({str(r): 1 for r in (run.relics or [])})
    # 默认挂一份按普攻算的乘区；技能的那一份在出手时按技能元素重算
    builder = mods_builder(attrs)
    session.pet_mods_builder = builder
    session.pet_default_element = str(player_element(attrs))
    session.pet_combat_mods = builder()
    # 护盾获取：入场按生命上限的固定比例折算成护盾基数，再由 shield_gain 放大
    session.grant_opening_shield(round(float(attrs.get("hp", 40)) * SHIELD_BASE_PCT))
    defend = (session.cfg.get("battle") or {}).get("defend") or {}
    session.meditate_pct = float(defend.get("meditate_pct", 0.40) or 0.40)


def _resist_layer(attrs: dict[str, Any]) -> dict[str, Any]:
    """把元素抗性原值换算成减伤系数，供 apply_to_session 直接查表。"""
    raw = attrs.get("element_resist") or attrs.get("res") or 0
    if not isinstance(raw, dict):
        return {}
    return {"element_resist": {k: resistance_scale(k, {k: v}) for k, v in raw.items()}}


def submit(
    run: RogueRun, action: dict[str, Any] | None, cfg: dict[str, Any] | None = None
) -> dict[str, Any]:
    """玩家下达一条指令，推进战斗。"""
    from .config import load_endless_config

    cfg = cfg or load_endless_config()
    data = run.pending.get("session")
    if not data:
        return {"ended": True, "victory": False, "log": [], "error": "no_session"}
    rng = random.Random(f"{run.seed}:{run.floor}:battle")
    avatar = _avatar(run)
    monster = dict(run.pending.get("monster") or {})
    mp_cap = run.mp_max
    state = {
        "hp": run.hp,
        "hp_max": run.hp_max,
        "mp": mp_cap,
        "mp_max": mp_cap,
        "buffs": {"atk": 0, "def": 0, "spd": 0},
        "relics": [],
    }
    log: list[tuple[int, str]] = []
    session = BattleSession(avatar, state, monster, cfg, rng, log=log)
    session.manual = True
    # 每次续战都要重新挂：load_session 只恢复血/蓝/条，不恢复局内属性
    _attach_attrs(session, run, monster)
    if not load_session(session, data):
        return {"ended": True, "victory": False, "log": [], "error": "bad_session"}
    _tally_action(run, action)
    session.submit_action(action)
    return _after_step(run, session, monster, cfg)


def _apply_timeout_damage(session: BattleSession, cfg: dict[str, Any]) -> int:
    """回合超上限后的每回合扣血（§12.4）。

    伤害值 = 当前回合数，元素从 8 种随机取——走普通伤害公式，
    玩家的 def / 抗性 / 闪避照常生效，所以它不是真伤。
    """
    limit = int((cfg.get("battle") or {}).get("max_rounds", 100) or 100)
    if session.turn <= limit:
        return 0
    element = session.rng.choice(list(ENEMY_TIMEOUT_ELEMENTS))
    # 命中威力（护盾吃掉的部分也算扣血，超时惩罚不该被盾白嫖）
    hit, _taken = session._enemy_damage_to_pet(int(session.turn), element=str(element))
    return int(hit)


def _tally_action(run: RogueRun, action: dict[str, Any] | None) -> None:
    """累计本场玩家发了哪几类指令（供埋点统计）。"""
    kind = str((action or {}).get("kind") or "attack")
    tally = dict(run.pending.get("_acts") or {})
    tally[kind] = int(tally.get(kind, 0) or 0) + 1
    run.pending["_acts"] = tally


def _after_step(
    run: RogueRun,
    session: BattleSession,
    monster: dict[str, Any],
    cfg: dict[str, Any] | None = None,
    *,
    count_step: bool = True,
) -> dict[str, Any]:
    """把进度写回 run；判断战斗是否结束。"""
    run.hp = max(0, session.pet_hp)
    # 魔力不回写：它只活在战斗里，下一场照旧满蓝
    timed_out = (
        session.frames >= session.max_frames and session._pick_target() is not None
    )
    ended = session.pet_hp <= 0 or session._pick_target() is None or timed_out
    victory = session.pet_hp > 0 and session._pick_target() is None
    if count_step:
        run.pending["_steps"] = int(run.pending.get("_steps") or 0) + 1
    steps = int(run.pending.get("_steps") or 0)
    if ended:
        hp_before = int(run.pending.pop("_hp_before", run.hp_max) or run.hp_max)
        acts = dict(run.pending.pop("_acts", None) or {})
        run.pending.pop("_steps", None)
        from .floor import floor_kind

        telemetry.battle(
            run.run_id,
            floor=run.floor,
            enemy_kind=str(monster.get("kind") or "normal"),
            floor_kind=floor_kind(run.floor),
            frames=session.frames,
            steps=steps,
            victory=victory,
            timed_out=timed_out,
            hp_before=hp_before,
            acts=acts,
            pet_hp_pct=(run.hp / run.hp_max) if run.hp_max else 0.0,
        )
        run.pending.pop("session", None)
        run.pending.pop("monster", None)
        run.pending.pop("battle_log", None)
    else:
        run.pending["session"] = dump_session(session)
        run.pending["monster"] = monster
        # 本步战报也存一份：回执要渲染它，渲染层不该再去重跑一遍战斗
        run.pending["battle_log"] = [[int(r), str(t)] for r, t in (session.log or [])]
    return {
        "ended": ended,
        "victory": victory,
        "timed_out": bool(timed_out),
        "kind": str(monster.get("kind") or "normal"),
        "pet_hp": run.hp,
        "enemy_hp": sum(max(0, int(r["unit"]["hp"])) for r in session.units),
        "log": list(session.log or []),
    }


def _slot_full(run: RogueRun, gear_id: str) -> bool:
    """这件装备对应的槽位是否已经满了。"""
    from .gear import SLOT_COUNT, gear_specs

    spec = gear_specs().get(str(gear_id)) or {}
    slot = str(spec.get("slot") or "")
    worn = list((run.equip or {}).get(slot) or []) if slot else []
    return len(worn) >= SLOT_COUNT.get(slot, 1)


def _equip_or_stock(run: RogueRun, gear_id: str) -> str | None:
    """掉落装备进包：**只在有空槽时自动穿上**，否则直接进库存。

    不自动换装——换装是玩家的构筑决定，系统替他换掉一件正在穿的装备，
    等于把他的选择权拿走了（而且他往往根本没看见掉了什么）。
    换下来的那件事仍然保留：这是「空槽自动装」而不是「永远不装」。
    返回被换下的 id（真实只会是 None，留着给调用方判断）。
    """
    from .gear import SLOT_COUNT, gear_specs

    spec = gear_specs().get(str(gear_id)) or {}
    slot = str(spec.get("slot") or "")
    worn = list((run.equip or {}).get(slot) or []) if slot else []
    if len(worn) >= SLOT_COUNT.get(slot, 1):
        run.pending.setdefault("gear_stock", []).append(str(gear_id))
        return None

    from .gear import equip as equip_gear

    equipped, replaced = equip_gear(run.equip, str(gear_id))
    run.equip = equipped
    if replaced:
        run.pending.setdefault("gear_stock", []).append(replaced)
    return replaced


def drop_rewards(
    run: RogueRun,
    *,
    elite: bool = False,
    boss: bool = False,
    elite_fight: bool = False,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """战斗掉落：**每场必掉一件装备** + 遗物按概率。

    装备是平值属性的主来源（§7.5），所以它是必掉、且取「当前解锁的最高档」；
    槽位在武器/防具/饰品之间等概率，槽内再等概率抽一件。
    遗物仍按 §6.7 的概率与档位规则。
    """
    from .floor import stage_of
    from .gear import roll_gear
    from .relics import pick_relic, relic_max_tier

    rng = rng or random.Random(f"{run.seed}:{run.floor}:drop")
    stage = stage_of(run.floor)
    unlocked = relic_max_tier(stage)
    source = "boss" if boss else ("elite" if elite else "normal")
    out: dict[str, Any] = {
        "relic": None,
        "gear": None,
        "source": source,
        "tier": None,
        "stopped": None,
    }

    # 掉率与档位（文档 §6.7 + 12 号平衡）：
    #   普通/强化 → 概率掉，档位取「1 ~ 已解锁」
    #   精英/首领 → 必掉，档位取「已解锁 + 1」（封顶 T5）
    if elite or boss:
        chance = 1.0
        tiers = [min(5, unlocked + 1)]
    else:
        chance = ELITE_FIGHT_CHANCE if source == "elite_fight" else NORMAL_CHANCE
        tiers = list(range(1, unlocked + 1))
    # 装备必掉：先结算装备，再结算遗物（两件事互不影响）
    gear_rng = random.Random(f"{run.seed}:{run.floor}:gear")
    gear = roll_gear(gear_rng, stage)
    if gear is not None:
        gear_id = str(gear.get("id"))
        out["gear"] = gear_id
        # 空槽自动穿；槽满只进库存，**不替玩家换装**
        out["auto_equipped"] = not _slot_full(run, gear_id)
        _equip_or_stock(run, gear_id)
        from .gear import gear_specs

        spec = gear_specs().get(str(gear_id)) or {}
        telemetry.gear_gain(
            run.run_id,
            floor=int(run.floor),
            gear_id=gear_id,
            slot=str(spec.get("slot") or ""),
            tier=int(spec.get("tier", 1) or 1),
            source=source,
            auto_equipped=bool(out["auto_equipped"]),
        )

    if not tiers or rng.random() >= chance:
        return out

    tier = rng.choice(tiers)
    owned = {str(r): 1 for r in (run.relics or [])}
    spec = pick_relic(
        random.Random(f"{run.seed}:{run.floor}:drop:pick"), stage, owned, tier=tier
    )
    if spec is None:
        return out
    relic_id = str(spec.get("id"))
    run.relics.append(relic_id)
    out["relic"] = relic_id
    out["tier"] = int(spec.get("tier", tier) or tier)
    telemetry.relic_gain(
        run.run_id,
        floor=int(run.floor),
        relic_id=relic_id,
        tier=int(spec.get("tier", tier) or tier),
        set_key=str(spec.get("set") or ""),
        source=source,
    )
    return out


def battle_actions(run: RogueRun) -> list[dict[str, Any]]:
    """当前可用的战斗指令（供界面列编号）。

    技能标签带耗魔与冷却：不写的话玩家没法判断这一下值不值，
    也看不出为什么某个技能点不动（多半是蓝不够或在冷却）。
    """
    out = [{"key": "1", "label": "普攻", "action": dict(ACTION_ATTACK)}]
    specs = skill_specs()
    for index, skill_id in enumerate(
        [str(s) for s in (run.skills.get("active") or [])][:4], 2
    ):
        spec = specs.get(skill_id) or {}
        name = str(spec.get("name") or skill_id)
        note = skill_cost_note(spec)
        ok, reason = _skill_usable(run, spec)
        label = f"{name}（{note}）" if note else name
        if not ok:
            label += "·" + reason
        out.append(
            {
                "key": str(index),
                "label": label,
                "action": {"kind": "skill", "skill": skill_id},
            }
        )
    out.append(
        {"key": str(len(out) + 1), "label": "防御", "action": dict(ACTION_DEFEND)}
    )
    out.append(
        {"key": str(len(out) + 1), "label": "冥想", "action": dict(ACTION_MEDITATE)}
    )
    return out


def _skill_usable(run: RogueRun, spec: dict[str, Any]) -> tuple[bool, str]:
    """这个技能现在能不能放；不能时给一句原因。

    魔力只活在战斗里：蓝量从当前会话的存档读，没有会话就不判蓝。
    """
    data = (run.pending or {}).get("session") or {}
    if not data:
        return True, ""
    skill_id = str(spec.get("id") or "")
    cd = cooldown_left(data, skill_id)
    if cd > 0:
        return False, f"冷却剩{cd}"
    need = int(spec.get("mp_cost", 0) or 0)
    have = int(data.get("pet_mp", 0) or 0)
    if need > have:
        return False, f"蓝不足（差 {need - have}）"
    return True, ""


def _cooldown_unit() -> int:
    """冷却一「格」折成几帧，与 ``battle.cooldown_frames`` 同源。"""
    from .config import load_endless_config

    battle = load_endless_config().get("battle") or {}
    return max(1, int(battle.get("cooldown_frames", 0) or 0) or 10)


def cooldown_left(data: dict[str, Any], skill_id: str) -> int:
    """还要等几次出手才能再放（按帧折算，向上取整）。"""
    frames = int((data.get("cooldowns") or {}).get(str(skill_id), 0) or 0)
    if frames <= 0:
        return 0
    return max(1, -(-frames // _cooldown_unit()))


def skill_cost_note(spec: dict[str, Any]) -> str:
    """耗魔。不耗魔也写「无耗魔」，只留名字时玩家判断不了这一下花不花钱。"""
    cost = int(spec.get("mp_cost", 0) or 0)
    return f"魔{cost}" if cost else "无耗魔"
