"""肉鸽爬塔系统（任务书 §12 的正式实现）。

结构：100 层塔 = 5 区域 × 20 层。普通层三选一（战斗/事件/商店）；区域中点固定
遗物三选一；首领前一层唯一内容是回血补给；首领层唯一内容是**随机一个候选首领**。
run 状态持久化在 ``PETROGUE.run_state``，退出可续。局内生命/金币/增益只存在于 run
状态，**只在结算时刻**（:func:`settle_run`）写回养成数据与轮回成就点——§12.4 红线。

战斗为 **ATB 攻击条**模型：个人攻击条上限 100，每轮按速度攒 ``gauge_rate_mult × spd``，
先满者出击（速度值决定出击频率）；战斗计时条每轮固定 +10，每满 100 触发一次
「计时条满」——所有技能剩余冷却 -1、计时 Buff 剩余时长 -1（预留）。
玩家的决策点在路线、遗物/补给、事件、商店与继续/撤退。所有随机都以
``floor_rng(seed, floor, purpose)`` 派生，同 seed 同层可复现。
"""

import random
from datetime import datetime
from typing import Any

from .interaction import check_action_point, consume_action_point
from .skills import get_skill_power, increase_proficiency

"""行动点键（见 daily.yaml action_points.costs 与 roguelike.yaml）。"""
ENTRY_COST_KEY = "rogue"
STEP_COST_KEY = "rogue_step"

BASE_SKILL_POWER = 30.0
"""技能威力基准（= skills.yaml 撞击 tackle 的 power）。

伤害技能的伤害系数 = ``get_skill_power(含熟练度加成) / BASE_SKILL_POWER``：
威力 30 → 1.0×（等于普攻但白赚熟练度），火球术 40 → 1.33×，肉鸽斩击 55 → 1.83×。
"""


def _load_rogue_config() -> dict[str, Any]:
    from ..config_loader import load_config

    return load_config("roguelike")


def _load_skills_config() -> dict[str, Any]:
    from ..config_loader import load_config

    return load_config("skills")


def _load_events_config() -> dict[str, Any]:
    from ..config_loader import load_config

    return load_config("rogue_events")


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
        int(region["boss_floor"])
        for region in cfg.get("tower", {}).get("regions", [])
    }


def elite_floors(run: dict[str, Any], region: dict[str, Any], cfg: dict[str, Any]) -> set[int]:
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
    # 前 gap 层（start .. start+gap-1）不生成；候选到 end-1（首领层不含）；
    # 补给层（首领前一层）没有战斗路线，也从候选排除——保底精英因此落在 end-2。
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
        # 保底精英必留，其余按区域 rng 洗牌后截断到上限。
        others = [floor for floor in picked if floor != guaranteed]
        rng.shuffle(others)
        keep = others[: max_count - (1 if guaranteed in picked else 0)]
        if guaranteed in picked:
            keep.append(guaranteed)
        picked = keep
    return set(picked)


def _is_elite_floor(run: dict[str, Any], region: dict[str, Any], cfg: dict[str, Any]) -> bool:
    """当前层的战斗路线是否为精英遭遇（首领层除外，见 :func:`elite_floors`）。"""
    return run["floor"] in elite_floors(run, region, cfg)


def _region_events(region: dict[str, Any]) -> list[dict[str, Any]]:
    """该区域的事件表（rogue_events.yaml 按区域 id 分组）。"""
    return (_load_events_config().get("events") or {}).get(region["id"], [])


def _load_relics_config() -> dict[str, Any]:
    from ..config_loader import load_config

    return load_config("rogue_relics")


def relic_specs() -> dict[str, dict[str, Any]]:
    """遗物表：{id: 遗物}（始终从 rogue_relics.yaml 读取，勿与塔配置混用）。"""
    return {
        spec["id"]: spec
        for spec in _load_relics_config().get("relics", [])
        if spec.get("id")
    }


def _run_relics(rogue) -> list[str]:
    """本局遗物（run 内生效）；PETROGUE.RELICS 只是图鉴，不提供加成。"""
    return list((rogue.run_state or {}).get("relics") or [])


def relic_multipliers(rogue) -> dict[str, float]:
    """收集**本局**遗物的乘区效果（同键叠乘）：atk/def/spd/mp/max_hp/coins/exp/vs_boss 的 *_mult 键与 shop_discount。"""
    specs = relic_specs()
    total: dict[str, float] = {}
    for relic_id in _run_relics(rogue):
        spec = specs.get(relic_id)
        if not spec:
            continue
        for key, value in (spec.get("effect") or {}).items():
            if key.endswith("_mult") or key == "shop_discount":
                total[key] = total.get(key, 1.0) * float(value)
    return total


def relic_stat_bonuses(relic_ids: list[str]) -> dict[str, float]:
    """本局遗物的**平值属性加成**（atk/def/spd/mp/crit_rate），战斗入场时叠加。

    与乘区键（*_mult）互补：平值键直接加在战斗属性上，见 skills/遗物注释。
    """
    specs = relic_specs()
    total: dict[str, float] = {
        key: 0.0 for key in ("atk", "def", "spd", "mp", "crit_rate")
    }
    for relic_id in relic_ids:
        spec = specs.get(relic_id)
        if not spec:
            continue
        for key, value in (spec.get("effect") or {}).items():
            if key in total:
                total[key] += float(value)
    return total


def relic_flat(rogue, key: str) -> float:
    """收集**本局**遗物的固定值效果（start_coins / boss_heal 等）之和。"""
    specs = relic_specs()
    return float(
        sum(
            float((specs[relic_id].get("effect") or {}).get(key, 0))
            for relic_id in _run_relics(rogue)
            if relic_id in specs
        )
    )


def relic_offer(rogue, floor: int, seed: int, count: int = 3) -> list[str]:
    """遗物奖励层的三选一：从**本局未持有**的普通遗物中按 seed 抽 ``count`` 件。"""
    specs = relic_specs()
    owned = set(_run_relics(rogue))
    pool = [
        relic_id
        for relic_id, spec in specs.items()
        if spec.get("rarity") == "normal" and relic_id not in owned
    ]
    if not pool:
        return []
    rng = floor_rng(seed, floor, "relic")
    rng.shuffle(pool)
    return pool[: min(count, len(pool))]


def _grant_relic(rogue, relic_id: str) -> dict[str, Any] | None:
    """获得一件遗物：写入**本局** run.relics（立即生效）并登记到图鉴 RELICS。

    获得时即时生效的效果在这里处理：max_hp 抬局内上限并同步回血、
    start_coins 立即入账（其余效果由战斗/结算实时读取）。调用方负责持久化。
    """
    spec = relic_specs().get(relic_id)
    if spec is None:
        return None
    run = rogue.run_state or {}
    run.setdefault("relics", [])
    if relic_id not in run["relics"]:
        run["relics"].append(relic_id)
    if relic_id not in rogue.relics:
        rogue.relics.append(relic_id)   # 图鉴：见过即记录
    rogue.updated_at = datetime.now()
    effect = spec.get("effect", {}) or {}
    if "max_hp" in effect and run:
        delta = int(effect["max_hp"])
        run["hp_max"] = int(run.get("hp_max", 0)) + delta
        run["hp"] = min(run["hp_max"], int(run.get("hp", 0)) + delta)
    if "start_coins" in effect and run:
        run["coins"] = int(run.get("coins", 0)) + int(effect["start_coins"])
    return spec


PASSIVE_STAT_KEYS = ("atk", "def", "spd", "mp", "max_hp", "crit_rate")


def passive_effects(pet, skills_cfg: dict[str, Any] | None = None) -> dict[str, float]:
    """汇总已学被动技能（type: passive）的 effect，返回战斗/局内加成。

    键见 skills.yaml 的被动注释：atk/def/spd/mp 加在入场战斗属性上、
    max_hp 加在局内生命上限（new_run）上、crit_rate 为我方输出暴击率。
    """
    cfg = skills_cfg if skills_cfg is not None else _load_skills_config()
    total: dict[str, float] = {key: 0 for key in PASSIVE_STAT_KEYS}
    for skill_id in pet.skills.get("passive", []):
        spec = cfg.get("skills", {}).get(skill_id)
        if not spec or spec.get("type") != "passive":
            continue
        for key, value in (spec.get("effect") or {}).items():
            if key in total:
                total[key] += float(value)
    return total


def _text(key: str, default: str = "", **fields: Any) -> str:
    from ..utils.texts import text

    return text(f"rogue.{key}", default, **fields)


async def get_or_create_rogue(player_id: str | int):
    """取回肉鸽进度行；不存在时按默认值建行。"""
    from ..database import Rogue, load_rogue, save_rogue

    rogue = load_rogue(player_id)
    if rogue is None:
        rogue = Rogue(player_id=str(player_id))
        save_rogue(rogue)
    return rogue


def floor_rng(seed: int, floor: int, purpose: str) -> random.Random:
    """每层每用途独立的随机源：确定性、可复现、互不干扰。"""
    return random.Random(f"{seed}:{floor}:{purpose}")


# ---------------------------------------------------------------------------
# run 生命周期
# ---------------------------------------------------------------------------

def new_run(pet, cfg: dict[str, Any], rogue=None) -> dict[str, Any]:
    """开一局：局内生命取养成 HP 上限（+被动 max_hp +遗物 max_hp_mult），从第 1 层开始。

    ``rogue`` 传入时应用遗物开局效果（max_hp_mult / start_coins）。
    """
    hp_max = int(pet.attributes.get("hp", 100)) + int(passive_effects(pet).get("max_hp", 0))
    coins = 0
    if rogue is not None:
        mults = relic_multipliers(rogue)
        hp_max = max(1, int(round(hp_max * mults.get("max_hp_mult", 1.0))))
        coins = int(relic_flat(rogue, "start_coins"))
    return {
        "seed": random.randint(1, 10**9),
        "floor": 1,
        "hp": hp_max,
        "hp_max": hp_max,
        "coins": coins,
        "exp": 0,
        "buffs": {"atk": 0, "def": 0, "spd": 0},
        "boss_wins": 0,
        "cleared": False,
        "unlocks": [],       # 本局 Boss 首通解锁（结算时落库）
        "relics": [],        # 本局遗物——只在本局生效，结算随 run 清空
        "seen_events": [],   # 本局已触发的事件 id——每个事件只触发一次
        "pending": {},       # 当前待应答状态
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }


def _has_run(rogue) -> bool:
    return bool(rogue.run_state)


async def start(pet, rogue) -> str:
    """入场：扣入场行动点，开新 run 并展示第 1 层。"""
    if _has_run(rogue):
        return await resume(pet, rogue)
    cfg = _load_rogue_config()
    try:
        check_action_point(pet, ENTRY_COST_KEY)
    except ValueError as exc:
        return _text("entry_no_ap", str(exc), cost=_entry_cost())
    consume_action_point(pet, ENTRY_COST_KEY)

    rogue.run_state = new_run(pet, cfg, rogue)
    _present_floor(rogue.run_state, cfg, rogue)
    await _persist(rogue)
    return _text(
        "started",
        "🗼 入场成功！",
        floor=rogue.run_state["floor"],
        action_points=pet.action_points,
        coins=rogue.run_state["coins"],
    ) + "\n" + _pending_text(rogue.run_state)


async def resume(pet, rogue) -> str:
    """继续/重发当前局的待应答提示（掉线重连、行动点恢复后重试）。"""
    if not _has_run(rogue):
        return _text("no_run", "当前没有进行中的局。发送「/宠物.肉鸽 开始」入场。")
    run = rogue.run_state
    pending = run.get("pending") or {}
    if pending.get("type") == "need_ap":
        # 重试前进（扣步进点）。
        return await _advance(pet, rogue, first=False)
    await _persist(rogue)
    return _progress_line(run) + "\n" + _pending_text(run)


def progress(rogue) -> str:
    """历史、本局遗物与图鉴概览（无局时只报历史与图鉴）。"""
    lines = [_text("stats", "历史统计", best=rogue.best_floor, runs=rogue.total_runs, clears=rogue.clears)]
    lines.append(
        _text(
            "codex",
            "📇 遗物图鉴：{owned}/{total}",
            owned=len(rogue.relics),
            total=len(relic_specs()),
        )
    )
    if _has_run(rogue):
        lines.insert(0, _progress_line(rogue.run_state))
        run_relics = _run_relics(rogue)
        if run_relics:
            names = "、".join(
                relic_specs().get(relic_id, {}).get("name", relic_id)
                for relic_id in run_relics
            )
            lines.insert(
                1,
                _text(
                    "run_relics",
                    "🗡 本局遗物（{count} 件，仅本局生效）：{names}",
                    count=len(run_relics),
                    names=names,
                ),
            )
    else:
        lines.insert(0, _text("no_run", "当前没有进行中的局。发送「/宠物.肉鸽 开始」入场。"))
    return "\n".join(lines)


def _progress_line(run: dict[str, Any]) -> str:
    region = region_of(run["floor"], _load_rogue_config())
    return _text(
        "active",
        "进行中的局",
        floor=run["floor"],
        region=region.get("name", ""),
        hp=run["hp"],
        hp_max=run["hp_max"],
        coins=run["coins"],
        exp=run["exp"],
    )


async def retreat(pet, rogue) -> str:
    """撤退：立即按到达层数结算并结束本局。"""
    if not _has_run(rogue):
        return _text("no_run", "当前没有进行中的局。发送「/宠物.肉鸽 开始」入场。")
    return await settle_run(pet, rogue, reason="retreat")


# ---------------------------------------------------------------------------
# 楼层展示与推进
# ---------------------------------------------------------------------------

def _present_floor(run: dict[str, Any], cfg: dict[str, Any], rogue=None) -> None:
    """生成当前层的待应答状态（写入 run['pending']，由 _pending_text 渲染）。

    - 区域末层（首领层）**只有一个内容：随机一个候选首领**（seed 决定，可复现），
      直接进入首领战；
    - 首领前一层（supply 层）**只有一个内容：回血补给事件**（不再有三选一）；
    - 区域中点 ``relic_floor`` → ``relic_choice``（固定遗物三选一，选完前进）；
    - 其余层 战斗 / 事件 / 商店 三选一；战斗可能是精英遭遇（:func:`_is_elite_floor`）；
      事件只从本区域**尚未触发过**的池里抽（每事件一次 run 只触发一次）。
    """
    floor = run["floor"]
    region = region_of(floor, cfg)

    if floor in boss_floor_set(cfg):
        # 首领层：随机一个候选首领（不再三选一），直接摆好首领战。
        candidates = (cfg.get("bosses") or {}).get(region.get("id", ""), [])
        boss_index = 0
        if candidates:
            boss_index = floor_rng(run["seed"], floor, "boss").randrange(len(candidates))
        run["pending"] = {"type": "battle", "boss": True, "boss_index": boss_index}
        return

    supply_cfg = cfg.get("supply", {}) or {}
    s_offset = int(supply_cfg.get("floor_offset", 1) or 0)
    if s_offset and floor == int(region["end"]) - s_offset and supply_cfg.get("event"):
        # 首领前一层：唯一内容就是回血补给。
        run["pending"] = {
            "type": "event",
            "event_id": supply_cfg["event"]["id"],
            "supply": True,
        }
        return

    relic_floor = int(region.get("relic_floor") or 0)
    if relic_floor and floor == relic_floor:
        offer = (
            relic_offer(rogue, floor, run["seed"])
            if rogue is not None
            else []
        )
        run["pending"] = {"type": "relic_choice", "offer": offer}
        return

    rng = floor_rng(run["seed"], floor, "floor")
    options = ["battle", "event", "shop"]
    rng.shuffle(options)
    pending: dict[str, Any] = {"type": "floor", "options": options}
    if _is_elite_floor(run, region, cfg):
        pending["elite"] = True
    if "shop" in options:
        # 遗物商店：区域前 no_before_gap 层不刷出（前 5 层无商店）。
        gap = int(cfg.get("shop", {}).get("no_before_gap", 5))
        if floor < int(region["start"]) + gap:
            options.remove("shop")
    if "event" in options:
        events = _region_events(region)
        seen = set(run.get("seen_events") or [])
        pool = [event for event in events if event.get("id") not in seen] or events
        if pool:
            picked = pool[floor_rng(run["seed"], floor, "event").randrange(len(pool))]
            pending["event_id"] = picked["id"]
            # 标记已触发：同一 run 内每个事件只出现一次。
            run.setdefault("seen_events", []).append(picked["id"])
    if "shop" in options:
        # 遗物货架：普通 / 稀有 / 商店专属各 1 件（排除本局已持有）。
        pending["stock"] = _shop_stock(run, cfg, floor_rng(run["seed"], floor, "shop"))
    run["pending"] = pending


def _shop_stock(run: dict[str, Any], cfg: dict[str, Any], rng: random.Random) -> list[str]:
    """按 tier（normal/rare/shop）各抽 1 件遗物，排除本局已持有。"""
    shop_cfg = cfg.get("shop", {}) or {}
    tiers = shop_cfg.get("stock_tiers") or ["normal", "rare", "shop"]
    specs = relic_specs()
    owned = set(run.get("relics") or [])
    stock: list[str] = []
    for tier in tiers:
        pool = [
            relic_id
            for relic_id, spec in specs.items()
            if spec.get("rarity") == tier and relic_id not in owned and relic_id not in stock
        ]
        if pool:
            stock.append(pool[rng.randrange(len(pool))])
    return stock


async def _advance(pet, rogue, *, first: bool = False) -> str:
    """解决完一层后前进：扣步进点、层数 +1、展示下一层。"""
    cfg = _load_rogue_config()
    run = rogue.run_state
    if first:
        pass  # 入场已扣，直接展示
    else:
        try:
            check_action_point(pet, STEP_COST_KEY)
        except ValueError:
            run["pending"] = {"type": "need_ap"}
            await _persist(rogue)
            return _text(
                "step_no_ap",
                "行动点不足，原地待命；明天 00:00 重置后发送「/宠物.肉鸽 继续」。",
            )
        consume_action_point(pet, STEP_COST_KEY)

    old_region = region_of(run["floor"], cfg)
    run["floor"] += 1
    new_region = region_of(run["floor"], cfg)
    _present_floor(run, cfg, rogue)
    await _persist(rogue)
    rendered = _pending_text(run)
    if new_region["id"] != old_region["id"]:
        # 跨区域：先播报新区域的主题，再给当前层提示。
        entered = _text(
            "region_enter",
            "🗺 抵达新区域【{name}】{desc}",
            name=new_region.get("name", ""),
            desc=new_region.get("desc", ""),
        )
        return entered + "\n" + rendered
    return rendered


def _pending_text(run: dict[str, Any]) -> str:
    """把当前 pending 渲染成一条可回复的消息。"""
    pending = run.get("pending") or {}
    ptype = pending.get("type")
    cfg = _load_rogue_config()
    floor = run["floor"]
    region = region_of(floor, cfg)

    if ptype == "need_ap":
        return _text("need_ap", "行动点不足，原地待命；明天 00:00 重置后发送「/宠物.肉鸽 继续」。")

    if ptype == "relic_choice":
        specs = relic_specs()
        lines = [_text("relic_choice", "🎁 遗物奖励层（回复编号三选一）：")]
        offer = pending.get("offer") or []
        if not offer:
            lines.append(_text("relic_none", "遗物已集齐，直接前进。"))
        for idx, relic_id in enumerate(offer, start=1):
            spec = specs.get(relic_id, {})
            lines.append(
                _text(
                    "relic_option",
                    "{idx}. {name} — {desc}",
                    idx=idx,
                    name=spec.get("name", relic_id),
                    desc=spec.get("desc", ""),
                )
            )
        return "\n".join(lines)

    if ptype == "battle":
        monster = _gen_monster(
            floor, region, cfg, floor_rng(run["seed"], floor, "battle"),
            boss_index=pending.get("boss_index"),
            elite=bool(pending.get("elite")),
        )
        if pending.get("boss"):
            # 首领层唯一内容 = 随机一个候选首领（此处只需报告随机结果并开战）。
            return _text(
                "boss_floor",
                "🎲 本层首领随机选定：{monster}！回复「1」开战。",
                floor=floor,
                monster=monster["name"],
                region=region.get("name", ""),
            )
        # 仅旧存档会走到这里：新流程普通/精英遭遇选中路线即开战，不再确认。
        return _text("battle_prompt", "回复「1」迎战。", monster=monster["name"])

    if ptype == "floor":
        labels = {"battle": _text("opt_battle", "① 战斗"), "event": _text("opt_event", "② 事件"), "shop": _text("opt_shop", "③ 商店")}
        is_elite = bool(pending.get("elite"))
        monster = _gen_monster(
            floor, region, cfg, floor_rng(run["seed"], floor, "battle"), elite=is_elite
        )
        stock = _stock_preview(pending, cfg)
        lines = [
            _text(
                "floor_header",
                "—— 第 {floor} 层 · {region} ——",
                floor=floor,
                region=region.get("name", ""),
            )
        ]
        for idx, opt in enumerate(pending.get("options", []), start=1):
            line = f"{idx}. {labels.get(opt, opt)}"
            if opt == "battle":
                if is_elite:
                    # 精英遭遇预告：让玩家知道这条战斗路线是精英战。
                    line += _text("elite_hint", "（⚠️ 精英出没：{monster}）", monster=monster["name"])
                else:
                    line += _text("battle_hint", f"（强敌：{monster['name']}）", monster=monster["name"])
            elif opt == "shop":
                line += _text("shop_hint", f"（{stock}）", stock=stock)
            else:
                line += _text("event_hint", "（未知遭遇）")
            lines.append(line)
        lines.append(_text("floor_reply", "回复编号选择路线。"))
        return "\n".join(lines)

    if ptype == "event":
        event = _find_event(pending.get("event_id"), region, cfg)
        if event is None:
            return _text("event_missing", "事件丢失，继续前进。")
        lines = [_text("event_prompt", "🎲 遭遇事件，回复编号选择行动：", text=event["text"])]
        for idx, choice in enumerate(event.get("choices", []), start=1):
            lines.append(f"{idx}. {choice['text']}")
        return "\n".join(lines)

    if ptype == "shop":
        return _shop_text(run, pending, cfg)

    return _text("unknown", "状态异常，发送「/宠物.肉鸽 进度」查看。")


def _relic_price(spec: dict[str, Any], cfg: dict[str, Any]) -> int:
    """遗物价格按 rarity 从 shop.tier_price 取（缺配置时 25）。"""
    tier_price = cfg.get("shop", {}).get("tier_price") or {}
    return int(tier_price.get(spec.get("rarity", "normal"), 25))


def _tier_label(tier: str) -> str:
    from ..utils.texts import text

    return text(f"tiers.{tier}", tier, tier=tier)


def _stock_preview(pending: dict[str, Any], cfg: dict[str, Any]) -> str:
    """路线预览里的遗物货架摘要（名称+价格）。"""
    names = []
    for relic_id in pending.get("stock", []):
        spec = relic_specs().get(relic_id)
        if spec:
            names.append(f"{spec['name']}({_relic_price(spec, cfg)}金)")
    return "、".join(names) or _text("shop_empty", "空空如也")


def _shop_text(run: dict[str, Any], pending: dict[str, Any], cfg: dict[str, Any]) -> str:
    """遗物商店货架：每行 = 名称（稀有度）— 价格。"""
    lines = [_text("shop_header", "🛒 遗物商店（金币 {coins}）", coins=run["coins"])]
    for idx, relic_id in enumerate(pending.get("stock", []), 1):
        spec = relic_specs().get(relic_id)
        if not spec:
            continue
        lines.append(
            _text(
                "relic_shop_line",
                "{idx}. {name}（{label}）— {price}金",
                idx=idx,
                name=spec.get("name", relic_id),
                label=_tier_label(spec.get("rarity", "normal")),
                price=_relic_price(spec, cfg),
            )
        )
    lines.append(_text("shop_reply", "回复编号购买，回复 0 离开。"))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 编号应答：局内交互入口
# ---------------------------------------------------------------------------

async def handle_number(pet, rogue, number: int) -> str:
    """处理局内的编号回复（路线/事件/商店/迎战/行动点待命）。"""
    if not _has_run(rogue):
        return _text("no_run", "当前没有进行中的局。发送「/宠物.肉鸽 开始」入场。")
    run = rogue.run_state
    pending = run.get("pending") or {}
    ptype = pending.get("type")
    cfg = _load_rogue_config()

    if ptype == "need_ap":
        return await _advance(pet, rogue)

    if ptype == "relic_choice":
        # 遗物奖励层：三选一即获得（入本局 run.relics 立即生效 + 登记图鉴）并前进。
        offer = pending.get("offer") or []
        if not offer:
            return await _advance(pet, rogue)
        if not 1 <= number <= len(offer):
            return _text("invalid_choice", "编号超出范围。", options=str(len(offer)))
        relic_id = offer[number - 1]
        if relic_id in _run_relics(rogue):
            return _text("relic_owned", "这件遗物本局已经拥有了。")
        spec = _grant_relic(rogue, relic_id) or {}
        await _persist(rogue)
        gained = _text(
            "relic_gained",
            "✨ 获得遗物（本局生效）：{name} — {desc}",
            name=spec.get("name", relic_id),
            desc=spec.get("desc", ""),
        )
        return gained + "\n" + await _advance(pet, rogue)

    if ptype == "battle":
        if number != 1:
            return _text("invalid", "回复「1」迎战。")
        return await _do_battle(pet, rogue, cfg)

    if ptype == "floor":
        options = pending.get("options", [])
        if not 1 <= number <= len(options):
            return _text("invalid_choice", "编号超出范围。", options=str(len(options)))
        choice = options[number - 1]
        if choice == "battle":
            # 选中路线即开战（普通/精英遭遇均不再需要一次"回复1迎战"的确认）。
            # pending 保持 floor 类型，_do_battle 从中读取 elite / boss_index。
            return await _do_battle(pet, rogue, cfg)
        if choice == "event":
            run["pending"] = {"type": "event", "event_id": pending.get("event_id")}
            await _persist(rogue)
            return _pending_text(run)
        run["pending"] = {"type": "shop", "stock": pending.get("stock", [])}
        await _persist(rogue)
        return _pending_text(run)

    if ptype == "event":
        return await _resolve_event(pet, rogue, cfg, number)

    if ptype == "shop":
        if number == 0:
            return await _advance(pet, rogue)
        return await _buy(pet, rogue, cfg, number)

    return _text("unknown", "状态异常，发送「/宠物.肉鸽 进度」查看。")


async def _resolve_event(pet, rogue, cfg: dict[str, Any], number: int) -> str:
    run = rogue.run_state
    pending = run.get("pending") or {}
    event = _find_event(pending.get("event_id"), region_of(run["floor"], cfg), cfg)
    if event is None:
        return await _advance(pet, rogue)
    choices = event.get("choices", [])
    if not 1 <= number <= len(choices):
        return _text("invalid_choice", "编号超出范围。", options=str(len(choices)))

    effect = choices[number - 1].get("effect", {}) or {}
    lines = [_text("event_result", "→ {choice}", choice=choices[number - 1]["text"])]
    coins = int(effect.get("coins", 0))
    if coins:
        run["coins"] = max(0, run["coins"] + coins)
        lines.append(_text("ev_coins", "局内金币 {delta:+d}（现有 {coins}）", delta=coins, coins=run["coins"]))
    hp_delta = int(effect.get("run_hp", 0))
    if hp_delta and pending.get("supply"):
        # 补给的回血按区域 heal_mult 放大，保证后期首领战前仍有效。
        region = region_of(run["floor"], cfg)
        heal_mult = float((region.get("shop") or {}).get("heal_mult", 1))
        hp_delta = int(round(hp_delta * heal_mult))
    if hp_delta:
        # 事件伤害不致死（下限 1），死亡只来自战斗。
        run["hp"] = max(1, min(run["hp_max"], run["hp"] + hp_delta))
        lines.append(_text("ev_hp", "局内生命 {delta:+d}（现有 {hp}/{hp_max}）", delta=hp_delta, hp=run["hp"], hp_max=run["hp_max"]))
    exp = int(effect.get("exp", 0))
    if exp:
        run["exp"] = max(0, run["exp"] + exp)
        lines.append(_text("ev_exp", "本局经验 {delta:+d}（累计 {exp}）", delta=exp, exp=run["exp"]))
    # 事件专属遗物掉落（rogue_events.yaml 的 effect.relic）：入本局并登记图鉴。
    relic_id = effect.get("relic")
    if relic_id and relic_id not in _run_relics(rogue):
        spec = _grant_relic(rogue, str(relic_id)) or {}
        await _persist(rogue)
        lines.append(
            _text(
                "relic_gained",
                "✨ 获得遗物（本局生效）：{name} — {desc}",
                name=spec.get("name", relic_id),
                desc=spec.get("desc", ""),
            )
        )

    advance = await _advance(pet, rogue)
    return "\n".join(lines) + "\n" + advance


async def _buy(pet, rogue, cfg: dict[str, Any], number: int) -> str:
    """遗物商店购买：按 tier 价格扣金币，遗物入本局并立即登记图鉴。"""
    run = rogue.run_state
    pending = run.get("pending") or {}
    stock = pending.get("stock", [])
    if not 1 <= number <= len(stock):
        return _text("invalid_choice", "编号超出范围。回复 0 离开。", options=str(len(stock)))
    spec = relic_specs().get(stock[number - 1])
    if spec is None:
        return _text("shop_missing", "这件商品不见了。")
    if spec.get("id") in _run_relics(rogue):
        return _text("relic_owned", "这件遗物本局已经拥有了。")
    price = _relic_price(spec, cfg)
    # 遗物 shop_discount 价格乘区（<1 更便宜）。
    discount = relic_multipliers(rogue).get("shop_discount", 1.0)
    price = max(1, int(round(price * discount)))
    if run["coins"] < price:
        return _text("shop_poor", "金币不足（需要 {price}，现有 {coins}）。", price=price, coins=run["coins"])

    run["coins"] -= price
    hp_max_before = int(run.get("hp_max", 0))
    coins_before = int(run["coins"])
    _grant_relic(rogue, spec.get("id"))   # 入本局 + 图鉴；max_hp/start_coins 即时生效
    lines = [
        _text(
            "shop_bought",
            "已购买 {name}（-{price} 金，剩余 {coins}）",
            name=spec.get("name", ""),
            price=price,
            coins=run["coins"],
        ),
        _text(
            "relic_gained",
            "✨ 获得遗物（本局生效）：{name} — {desc}",
            name=spec.get("name", ""),
            desc=spec.get("desc", ""),
        ),
    ]
    if int(run.get("hp_max", 0)) > hp_max_before:
        lines.append(
            _text(
                "shop_hp_max",
                "局内生命上限 +{delta}（{hp}/{hp_max}）",
                delta=int(run["hp_max"]) - hp_max_before,
                hp=run["hp"],
                hp_max=run["hp_max"],
            )
        )
    if int(run.get("coins", 0)) > coins_before:
        lines.append(
            _text(
                "ev_coins",
                "局内金币 {delta:+d}（现有 {coins}）",
                delta=int(run["coins"]) - coins_before,
                coins=run["coins"],
            )
        )

    await _persist(rogue)
    lines.append(_shop_text(run, pending, cfg))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 战斗
# ---------------------------------------------------------------------------

def _gen_monster(
    floor: int,
    region: dict[str, Any],
    cfg: dict[str, Any],
    rng: random.Random,
    boss_index: int | None = None,
    elite: bool = False,
) -> dict[str, Any]:
    """按区域公式生成敌人；``elite`` 生成精英，``boss_index`` 生成候选首领。

    普通敌人 = 区域 base + growth ×（层 - 区域起始层），名字从该区域 10 种小怪里抽；
    精英 = 普通公式 × ``elite.stats_mult``，名字/描述从该区域 5 个精英备选里抽，
    掉落 × ``elite.reward_mult``（``elite: True`` 标记）；
    首领 = 当层普通公式 × 候选倍率，coins/exp 用候选的绝对值。区域间 base 成倍跳升，
    因此跨区域难度差距显著。
    """
    scale = floor - int(region["start"])
    base = region["monster"]["base"]
    growth = region["monster"]["growth"]
    rewards = region.get("rewards", {})
    monster = {
        "name": (region.get("monsters") or ["敌人"])[
            rng.randrange(len(region.get("monsters") or ["敌人"]))
        ],
        "hp": int(base["hp"] + growth["hp"] * scale),
        "atk": int(base["atk"] + growth["atk"] * scale),
        "def": int(base["def"] + growth["def"] * scale),
        "spd": int(base["spd"] + growth["spd"] * scale),
        "coins": int(rewards.get("coins", 2) + rewards.get("coins_per_floor", 1) * scale),
        "exp": int(rewards.get("exp", 20) + rewards.get("exp_per_floor", 4) * scale),
        "boss": False,
        "elite": False,
        "desc": "",
    }
    if elite:
        elite_cfg = cfg.get("elite", {}) or {}
        pool = region.get("elites") or []
        chosen = pool[rng.randrange(len(pool))] if pool else None
        if chosen is not None:
            monster["name"] = chosen.get("name", monster["name"])
            monster["desc"] = chosen.get("desc", "")
        monster["elite"] = True
        stats_mult = float(elite_cfg.get("stats_mult", 1.6))
        reward_mult = elite_cfg.get("reward_mult", {}) or {}
        for key in ("hp", "atk", "def", "spd"):
            monster[key] = max(1, int(round(monster[key] * stats_mult)))
        monster["coins"] = int(round(monster["coins"] * float(reward_mult.get("coins", 1))))
        monster["exp"] = int(round(monster["exp"] * float(reward_mult.get("exp", 1))))
    if boss_index is not None:
        candidates = (cfg.get("bosses") or {}).get(region.get("id", ""), [])
        if 0 <= boss_index < len(candidates):
            boss = candidates[boss_index]
            monster["boss"] = True
            monster["name"] = boss["name"]
            monster["desc"] = boss.get("desc", "")
            monster["hp"] = int(monster["hp"] * boss.get("hp_mult", 1))
            monster["atk"] = int(monster["atk"] * boss.get("atk_mult", 1))
            monster["def"] = int(monster["def"] * boss.get("def_mult", 1))
            monster["spd"] = int(monster["spd"] * boss.get("spd_mult", 1))
            coin_range = boss.get("coins", [monster["coins"], monster["coins"]])
            monster["coins"] = rng.randint(int(coin_range[0]), int(coin_range[1]))
            monster["exp"] = int(boss.get("exp", monster["exp"]))
    return monster


async def _do_battle(pet, rogue, cfg: dict[str, Any]) -> str:
    run = rogue.run_state
    floor = run["floor"]
    region = region_of(floor, cfg)
    rng = floor_rng(run["seed"], floor, "battle")
    pending = run.get("pending") or {}
    is_elite = bool(pending.get("elite"))
    monster = _gen_monster(
        floor, region, cfg, rng,
        boss_index=pending.get("boss_index"),
        elite=is_elite,
    )

    mults = relic_multipliers(rogue)
    pet_hp, pet_mp, victory, rounds, pet_acts, monster_acts, crits = _auto_battle(
        pet, run, monster, cfg, rng, mults
    )
    run["hp"] = max(0, pet_hp)

    if monster.get("boss"):
        header_key, header_default = "battle_header", "⚔️ 第 {floor} 层 · {region}：{name} VS {monster}"
    elif is_elite:
        header_key, header_default = "elite_header", "⚠️ 精英战 · 第 {floor} 层 · {region}：{name} VS {monster}"
    else:
        header_key, header_default = "battle_header", "⚔️ 第 {floor} 层 · {region}：{name} VS {monster}"
    lines = [
        _text(
            header_key,
            header_default,
            floor=floor,
            region=region.get("name", ""),
            name=pet.name,
            monster=monster["name"],
        )
    ]
    if monster.get("boss") or is_elite:
        # 首领/精英的登场描述（roguelike.yaml bosses[].desc / elites[].desc）。
        lines.append(_text("boss_desc", "—— {desc}", desc=monster.get("desc", "")))
    # 出击节奏：速度决定攻击条积攒，直接展示双方出手次数与暴击。
    lines.append(
        _text(
            "battle_pace",
            "⏱ 第 {rounds} 轮｜出击：你 {pa} 次 / 敌 {ma} 次（暴击 {crits} 次）",
            rounds=rounds,
            pa=pet_acts,
            ma=monster_acts,
            crits=crits,
        )
    )

    if not victory:
        lines.append(_text("battle_loss", "💀 战败……本局结束，按到达层数结算。", monster=monster["name"], rounds=rounds))
        await _persist(rogue)
        lines.append(await settle_run(pet, rogue, reason="defeat"))
        return "\n".join(lines)

    # 掉落吃遗物乘区（coins_mult / exp_mult）。
    coin_gain = int(round(monster["coins"] * mults.get("coins_mult", 1.0)))
    exp_gain = int(round(monster["exp"] * mults.get("exp_mult", 1.0)))
    run["coins"] += coin_gain
    run["exp"] += exp_gain
    lines.append(
        _text(
            "battle_win",
            "✅ 战胜 {monster}！（{rounds} 回合）局内金币 +{coins}（现有 {total}），本局经验 +{exp}",
            monster=monster["name"],
            rounds=rounds,
            coins=coin_gain,
            total=run["coins"],
            exp=exp_gain,
        )
    )

    if monster.get("boss"):
        # 遗物 boss_heal：击杀首领后回复局内生命。
        heal = int(relic_flat(rogue, "boss_heal"))
        if heal:
            before = run["hp"]
            run["hp"] = min(int(run.get("hp_max", run["hp"])), run["hp"] + heal)
            if run["hp"] > before:
                lines.append(
                    _text(
                        "relic_boss_heal",
                        "💚 遗物生效：回复 {amount} 局内生命",
                        amount=run["hp"] - before,
                    )
                )

    if floor in boss_floor_set(cfg):
        run["boss_wins"] += 1
        unlock = (cfg.get("boss_unlocks") or {}).get(str(floor))
        if unlock is not None:
            run.setdefault("unlocks", []).append(str(floor))
            lines.append(_text("boss_unlock_hint", "🔓 首通奖励将于结算时发放。", floor=floor))
        # Boss 专属遗物：一个 Boss 对应一件，击杀即得（入本局 run 立即生效 + 登记图鉴）。
        boss_index = pending.get("boss_index")
        candidates = (cfg.get("bosses") or {}).get(region.get("id", ""), [])
        if boss_index is not None and 0 <= boss_index < len(candidates):
            relic_id = candidates[boss_index].get("relic")
            if relic_id and relic_id not in _run_relics(rogue):
                spec = _grant_relic(rogue, relic_id) or {}
                await _persist(rogue)
                lines.append(
                    _text(
                        "boss_relic_gained",
                        "👑 Boss 专属遗物入手（本局生效）：{name} — {desc}",
                        name=spec.get("name", relic_id),
                        desc=spec.get("desc", ""),
                    )
                )
        if floor == int(cfg["tower"]["floors"]):
            run["cleared"] = True
            await _persist(rogue)
            lines.append(await settle_run(pet, rogue, reason="clear"))
            return "\n".join(lines)

    advance = await _advance(pet, rogue)
    return "\n".join(lines) + "\n" + advance


def _auto_battle(
    pet,
    run: dict[str, Any],
    monster: dict[str, Any],
    cfg: dict[str, Any],
    rng: random.Random,
    mults: dict[str, float] | None = None,
) -> tuple[int, int, bool, int, int, int, int]:
    """ATB 自动战斗。返回 (局内生命, 剩余MP, 是否胜利, 轮数, 我方出击, 敌方出击, 暴击次数)。

    攻击条机制：个人攻击条上限 100，每轮按速度攒 ``gauge_rate_mult × spd``，
    **先满者出击**（-100），同轮都满时速度快者先动——速度值决定出击频率。
    战斗计时条每轮结束固定 ``+timer_gain``，满 100 触发一次「计时条满」：
    所有技能剩余冷却 -1、计时 Buff 剩余时长 -1（预留），随后计时条清零。
    ``mults`` 为遗物乘区（relic_multipliers）：atk/def/spd/mp 作用于入场属性，
    vs_boss_mult 作用于对首领/精英的输出。
    """
    mults = mults or {}
    battle_cfg = cfg.get("battle", {})
    variance = battle_cfg.get("variance", [0.85, 1.15])
    def_factor = float(battle_cfg.get("def_factor", 0.5))
    max_rounds = int(battle_cfg.get("max_rounds", 50))
    gauge_rate_mult = float(battle_cfg.get("gauge_rate_mult", 5))
    timer_gain = float(battle_cfg.get("timer_gain", 10))
    crit_mult = float(battle_cfg.get("crit_mult", 1.8))
    skills_cfg = _load_skills_config()
    passives = passive_effects(pet, skills_cfg)

    attrs = pet.attributes
    buffs = run.get("buffs", {})
    # 遗物平值属性（本局 relic_stat_bonuses）+ 被动技能，都在入场属性上叠加。
    relic_stats = relic_stat_bonuses(run.get("relics") or [])
    pet_hp = run["hp"]
    pet_mp = int(int(attrs.get("mp", 20)) * mults.get("mp_mult", 1.0)) + int(passives["mp"]) + int(relic_stats["mp"])
    mp_cap = pet_mp
    pet_atk = int((int(attrs.get("atk", 10)) + int(buffs.get("atk", 0))) * mults.get("atk_mult", 1.0)) + int(passives["atk"]) + int(relic_stats["atk"])
    pet_def = int((int(attrs.get("def", 10)) + int(buffs.get("def", 0))) * mults.get("def_mult", 1.0)) + int(passives["def"]) + int(relic_stats["def"])
    pet_spd = int((int(attrs.get("spd", 10)) + int(buffs.get("spd", 0))) * mults.get("spd_mult", 1.0)) + int(passives["spd"]) + int(relic_stats["spd"])
    # 被动 max_hp 只抬局内上限（run["hp"] 不变），入局时由 new_run 统一抬。
    hp_max = int(run.get("hp_max", pet_hp))
    crit_rate = float(passives["crit_rate"]) + float(relic_stats["crit_rate"])
    crits = 0
    # 对首领/精英的输出乘区（遗物 vs_boss_mult）
    out_mult = (
        mults.get("vs_boss_mult", 1.0)
        if (monster.get("boss") or monster.get("elite"))
        else 1.0
    )

    active_pairs = [
        (skill_id, skills_cfg["skills"][skill_id])
        for skill_id in pet.skills.get("active", [])
        if skill_id in skills_cfg.get("skills", {})
    ]
    # 技能冷却：施放时置为 cooldown（单位=计时条周期数），每次「计时条满」全体 -1。
    cooldowns: dict[str, int] = {skill_id: 0 for skill_id, _ in active_pairs}

    def _damage(atk: int, defence: int) -> int:
        raw = (atk - defence * def_factor) * rng.uniform(*variance)
        return max(1, int(round(raw)))

    def _pet_damage(defence: int) -> int:
        """我方输出（遗物 vs_boss_mult 乘区 + 被动 crit_rate 暴击，暴击计数供战报展示）。"""
        nonlocal crits
        dmg = max(1, int(round(_damage(pet_atk, defence) * out_mult)))
        if crit_rate > 0 and rng.random() < crit_rate:
            dmg = max(1, int(round(dmg * crit_mult)))
            crits += 1
        return dmg

    def _heal_amount(skill: dict[str, Any]) -> int:
        """治疗技能的回复量；非治疗（power>0 或无 heal 效果）返回 0。"""
        if int(skill.get("power", 0)) != 0:
            return 0
        effects = skill.get("effects", []) or []
        return next(
            (int(e.get("amount", 0)) for e in effects if e.get("type") == "heal"), 0
        )

    gauge_pet = 0.0
    gauge_monster = 0.0
    battle_timer = 0.0
    pet_acts = monster_acts = 0
    rounds = 0
    while rounds < max_rounds and pet_hp > 0 and monster["hp"] > 0:
        rounds += 1
        # 攻击条：每轮按速度攒（gauge_rate_mult × spd），上限 100——速度决定出击频率。
        gauge_pet = min(100.0, gauge_pet + pet_spd * gauge_rate_mult)
        gauge_monster = min(100.0, gauge_monster + monster["spd"] * gauge_rate_mult)
        # 出击顺序：先满者动；同轮都满时速度快者先动。
        actors: list[tuple[str, int]] = []
        if gauge_pet >= 100.0:
            actors.append(("pet", pet_spd))
        if gauge_monster >= 100.0:
            actors.append(("monster", monster["spd"]))
        actors.sort(key=lambda a: -a[1])
        for who, _ in actors:
            if pet_hp <= 0 or monster["hp"] <= 0:
                break
            if who == "pet":
                gauge_pet -= 100.0
                pet_acts += 1
                # 1) 血量 < 50% 优先治疗；2) 否则按「威力×熟练度」从高到低放伤害技能
                #    （冷却中 / MP 不足的技能跳过）；3) 普攻。
                #    施放即涨熟练度，并按 skills.yaml 的 cooldown 进入冷却。
                acted = False
                if pet_hp < hp_max * 0.5:
                    for skill_id, skill in active_pairs:
                        if cooldowns.get(skill_id, 0) > 0:
                            continue
                        amount = _heal_amount(skill)
                        cost = int(skill.get("mp_cost", 0))
                        if amount and pet_mp >= cost:
                            pet_mp -= cost
                            pet_hp = min(hp_max, pet_hp + amount)
                            cooldowns[skill_id] = int(skill.get("cooldown", 0))
                            increase_proficiency(pet, skill_id, skills_config=skills_cfg)
                            acted = True
                            break
                if not acted:
                    ranked = sorted(
                        (
                            (sid, sk)
                            for sid, sk in active_pairs
                            if _heal_amount(sk) == 0 and cooldowns.get(sid, 0) <= 0
                        ),
                        key=lambda pair: -get_skill_power(pet, pair[0], skills_cfg),
                    )
                    for skill_id, skill in ranked:
                        cost = int(skill.get("mp_cost", 0))
                        if pet_mp < cost:
                            continue
                        pet_mp -= cost
                        factor = (
                            get_skill_power(pet, skill_id, skills_cfg) / BASE_SKILL_POWER
                        )
                        dealt = max(1, int(round(_pet_damage(monster["def"]) * factor)))
                        monster["hp"] -= dealt
                        # 回蓝效果（如聚魔弹 { type: mp }）：按技能 effects 回复，封顶入场 MP。
                        for effect in skill.get("effects", []) or []:
                            if effect.get("type") == "mp":
                                pet_mp = min(
                                    mp_cap, pet_mp + int(effect.get("amount", 0))
                                )
                        cooldowns[skill_id] = int(skill.get("cooldown", 0))
                        increase_proficiency(pet, skill_id, skills_config=skills_cfg)
                        acted = True
                        break
                if not acted:
                    monster["hp"] -= _pet_damage(monster["def"])
            else:
                gauge_monster -= 100.0
                monster_acts += 1
                pet_hp -= _damage(monster["atk"], pet_def)
        # 轮次结束：计时条固定 +timer_gain；每满 100 触发一次「计时条满」——
        # 所有技能剩余冷却 -1、计时 Buff 剩余时长 -1（预留），随后计时条清零。
        battle_timer += timer_gain
        if battle_timer >= 100.0:
            battle_timer -= 100.0
            for skill_id in cooldowns:
                if cooldowns[skill_id] > 0:
                    cooldowns[skill_id] -= 1
            # 计时 Buff（预留）：局内限时增益的剩余时长在此 -1（当前系统无 timed buff）。

    victory = monster["hp"] <= 0 and pet_hp > 0
    return pet_hp, pet_mp, victory, rounds, pet_acts, monster_acts, crits


# ---------------------------------------------------------------------------
# 结算（§12.2 唯一写回点）
# ---------------------------------------------------------------------------

async def settle_run(
    pet,
    rogue,
    *,
    reason: str = "retreat",
    rogue_config: dict[str, Any] | None = None,
    title_hook=None,
) -> str:
    """结束本局：按到达层数发成就点、经验写回、首通解锁写回，清空 run。

    这是 run 内容触碰养成数据的**唯一入口**（§12.4 红线）。
    """
    cfg = rogue_config if rogue_config is not None else _load_rogue_config()
    run = rogue.run_state or {}
    floors_reached = int(run.get("floor", 0))
    boss_wins = int(run.get("boss_wins", 0))
    cleared = bool(run.get("cleared", False))

    rewards = cfg.get("rewards", {})
    points = floors_reached * int(rewards.get("per_floor", 0))
    points += boss_wins * int(rewards.get("boss_bonus", 0))
    if cleared:
        points += int(rewards.get("clear_bonus", 0))

    lines = [
        _text(
            "settle_header",
            "📊 本局结算：到达第 {floor} 层{cleared}",
            floor=floors_reached,
            cleared=_text("settle_cleared", "，通关！" ) if cleared else "",
        )
    ]

    # 1) 经验写回并允许升级。
    exp_gain = int(run.get("exp", 0))
    if exp_gain > 0:
        from .leveling import check_level_up

        pet.exp += exp_gain
        level_result = check_level_up(pet)
        lines.append(_text("settle_exp", "经验 +{exp}", exp=exp_gain))
        if level_result.get("level_up"):
            lines.append(
                _text(
                    "settle_level",
                    "🎉 升到 {level} 级！获得 {talent_points} 天赋点",
                    level=level_result["new_level"],
                    talent_points=pet.talent_points,
                )
            )

    # 2) 首通解锁写回（幂等）。
    skills_cfg = _load_skills_config()
    for floor_key in dict.fromkeys(run.get("unlocks", [])):
        spec = (cfg.get("boss_unlocks") or {}).get(str(floor_key))
        if spec is None or floor_key in rogue.unlocked:
            continue
        rogue.unlocked.append(floor_key)
        kind = spec.get("type")
        if kind == "equipment":
            slot = spec.get("slot", "misc")
            pet.equipment[slot] = spec.get("item", {})
            lines.append(
                _text("unlock_equipment", "🔓 首通解锁装备：{name}", name=spec.get("item", {}).get("name", slot))
            )
        elif kind == "skill":
            from .skills import add_skill

            skill_id = spec.get("id", "")
            skill_name = skills_cfg.get("skills", {}).get(skill_id, {}).get("name", skill_id)
            if add_skill(pet, skill_id, skills_cfg):
                lines.append(_text("unlock_skill", "🔓 首通解锁技能：{name}", name=skill_name))
            else:
                lines.append(_text("unlock_skill_full", "🔓 已记录首通技能 {name}（技能槽已满，暂时无法学会）", name=skill_name))
        elif kind == "title":
            pet.title = spec.get("title", pet.title)
            lines.append(_text("unlock_title", "🔓 首通解锁称号：{title}", title=spec.get("title", "")))
            if title_hook is not None:
                from ..utils.callbacks import run_callback

                await run_callback(title_hook, pet, spec.get("title", ""))

    # 3) 成就点写回轮回数据。
    if points > 0:
        from .reincarnation import get_or_create_reincarnation

        reinc = await get_or_create_reincarnation(pet.player_id)
        reinc.achievement_points += points
        reinc.updated_at = datetime.now()
        from ..database import save_reincarnation

        save_reincarnation(reinc)
        lines.append(
            _text("settle_points", "成就点 +{points}（累计 {total}）", points=points, total=reinc.achievement_points)
        )

    # 4) 历史统计与 run 清空。
    rogue.total_runs += 1
    rogue.best_floor = max(rogue.best_floor, floors_reached)
    if cleared:
        rogue.clears += 1
    rogue.run_state = {}
    rogue.updated_at = datetime.now()
    await _persist(rogue)

    if reason == "retreat" and not cleared:
        lines.insert(0, _text("retired", "🏳️ 已撤退。"))
    return "\n".join(line for line in lines if line)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _entry_cost() -> int:
    from .interaction import _action_costs

    return int(_action_costs(None).get(ENTRY_COST_KEY, 0))


def _find_event(event_id: Any, region: dict[str, Any], cfg: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """在该区域的事件表（rogue_events.yaml 按区域分组）里按 id 查找事件。

    ``cfg`` 传入时额外支持 supply 回血补给事件（首领前一层的固定事件，
    定义在 roguelike.yaml 的 ``supply.event``）。
    """
    if cfg is not None:
        supply_event = (cfg.get("supply") or {}).get("event")
        if supply_event and supply_event.get("id") == event_id:
            return supply_event
    for event in _region_events(region):
        if event.get("id") == event_id:
            return event
    return None


async def _persist(rogue) -> None:
    from ..database import save_rogue

    save_rogue(rogue)
