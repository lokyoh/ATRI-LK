"""战斗引擎：ATB 攻击条模型、自动战斗与战报。"""

import random
from dataclasses import dataclass
from typing import Any

from ..affection import apply_affection_bonus
from ..skills import (
    equipped_specs,
    get_skill_power,
    increase_proficiency,
)
from ._common import (
    BASE_SKILL_POWER,
    _load_skills_config,
    _text,
    level_combat_mults,
    mp_regen_pct,
    rogue_mp_max,
    unactionable_cfg,
)
from .relics import relic_stat_bonuses
from .tower import (
    _enemy_skill_lines,
    _monster_desc,
    _monster_name,
    _monster_trait_line,
)


@dataclass
class BattleResult:
    """战斗结果。"""

    pet_hp: int
    pet_mp: int
    victory: bool
    rounds: int
    pet_acts: int
    monster_acts: int
    crits: int
    mp_cap: int = 0
    """局内魔力上限（供 ``_do_battle`` 写回 run，展示「MP x/y」）。"""

    enemy_hp: int = 0
    """敌方**全体**剩余生命（群怪是所有成员之和，不是单只）。"""
    enemy_hp_full: int = 0
    """敌方**全体**入场生命（含群怪全部成员）。"""
    enemy_units: int = 1
    """入场时的敌方单位数（群怪 > 1）。"""
    enemy_alive: int = 0
    """战斗结束时仍存活的敌方单位数。"""


def _mitigation_rate(defence: int, def_softness: float) -> float:
    """防御的百分比减伤率：``def / (n + def)``。

    ``n`` = ``battle.def_softness``（默认 100）。性质：

    * 防御 0 → 减伤 0%；防御 = n → 减伤 50%；防御 → ∞ 减伤趋近 100% 但**永远到不了**；
    * 边际递减：每点防御的收益递减，不会出现「堆到某个值就完全免疫」；
    * 与攻击力**无关**：旧公式 ``atk - def × def_factor`` 的等效减伤率是
      ``def × 0.5 / atk``，攻击越高防御越没用（深渊首领减伤只有 10%），
      而且前期攻击低时又会把伤害压到 1 点下限（几乎无敌）。
    """
    if defence <= 0:
        return 0.0
    n = max(1.0, float(def_softness))
    return float(defence) / (n + float(defence))


def _damage(
    atk: int,
    defence: int,
    variance: list[float],
    def_softness: float,
    rng: random.Random,
) -> int:
    """基础伤害公式（百分比减伤）。

    ``伤害 = atk × n / (n + def) × 浮动``

    保留伤害比例 = ``n / (n + def)``，即减伤率 = ``def / (n + def)``。
    最低 1 点，避免高防御把伤害压成 0（那样会变成无敌）。
    """
    keep = 1.0 - _mitigation_rate(defence, def_softness)
    raw = atk * keep * rng.uniform(*variance)
    return max(1, round(raw))


_PACK_MARKS = "①②③④⑤⑥⑦⑧⑨⑩"
"""群怪成员的编号后缀：让战报里一眼分出「哪一只在出手」。"""


def _pack_mark(index: int) -> str:
    """第 ``index`` 只群怪的后缀（1 起；超出范围回退为 ``#N``）。"""
    if 1 <= index <= len(_PACK_MARKS):
        return _PACK_MARKS[index - 1]
    return f"#{index}"


def _state_floor(state: Any) -> int:
    """从局内状态取出层号。

    战斗入口传进来的 ``run`` 是 ``rogue.run_state`` 的 **dict**，但
    :class:`RogueRun` 数据类也可能被直接传入（单元测试/内部复用），
    两种形态都要能读，否则等级战力会静默退化成「第 1 层」。
    """
    if isinstance(state, dict):
        value = state.get("floor", 1)
    else:
        value = getattr(state, "floor", 1)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


class BattleSession:
    """ATB 自动战斗引擎。

    攻击条机制：个人攻击条上限 100，每**帧**按速度攒 ``gauge_rate_mult × spd``，
    **先满者出击**（-100），同帧都满时速度快者先动——速度值决定出击频率。
    战斗计时条每帧结束固定 ``+timer_gain``，满 100 触发一次「计时条满」：
    所有技能剩余冷却 -1、计时 Buff 剩余时长 -1（预留），随后计时条清零。

    **帧（frame）与回合（turn）是两个单位，别混用**：

    * **帧**是内部时钟：攻击条积攒、冷却、DoT、削弱消退都按帧结算，
      循环上限也是帧（``max_frames = max_rounds × frames_per_turn``）。
    * **回合**是对外计时单位：战斗计时条走满一次算一回合，默认
      ``frames_per_turn = 100 / timer_gain = 10`` 帧。战报行首、胜/负/平局行的
      「N 回合」、``battle_pace`` 的「共 N 回合」全部用 :attr:`turn`。

    早期版本把帧号直接当回合显示，而 ATB 入场前几帧常无人攒满攻击条
    （例如速度 17 × 5 = 85 < 100，要两帧才出手），于是战报从「回合2」开始。
    现在展示一律走 :attr:`turn`（帧数 ÷ 10 向上取整），第 1 回合覆盖前 10 帧。

    敌方只有精英/首领（及其召唤物）会用专属技能（roguelike.yaml 的
    enemy_skills / enemy_phases）：蓄力终极技可被累积伤害打断、生命跌破阈值
    触发形态、召唤物受 battle.max_units 限制，本体倒下时召唤物一同消散。
    小怪 skills 为空，永远只有普攻。
    """

    def __init__(
        self,
        pet,
        run: dict[str, Any],
        monster: dict[str, Any],
        cfg: dict[str, Any],
        rng: random.Random,
        mults: dict[str, float] | None = None,
        log: list[tuple[int, str]] | None = None,
    ):
        self.pet = pet
        # 局内 run 状态 dict。注意不能叫 ``self.run``：那会遮蔽 ``run()``
        # 方法（自动战斗入口），导致 `_auto_battle` 调用时报
        # "TypeError: 'dict' object is not callable"。
        self.state = run
        self.monster = monster
        self.cfg = cfg
        self.rng = rng
        self.mults = mults or {}
        self.log = log

        # 战斗参数
        battle_cfg = cfg.get("battle", {})
        self.variance = battle_cfg.get("variance", [0.85, 1.15])
        self.def_softness = float(battle_cfg.get("def_softness", 100))
        self.max_rounds = int(battle_cfg.get("max_rounds", 500))
        self.gauge_rate_mult = float(battle_cfg.get("gauge_rate_mult", 5))
        # 速度 → 攻击条的曲线指数：1.0 = 线性（旧行为），0.5 = 开平方（增长变慢）。
        self.spd_exponent = float(battle_cfg.get("spd_exponent", 1.0))
        self.timer_gain = float(battle_cfg.get("timer_gain", 10))
        # 一个「回合」= 战斗计时条走满一次 = 内部跑多少帧。
        # 计时条每帧 +timer_gain、满 100 清零，所以默认 10 帧 = 1 回合。
        # 对外展示一律用回合（帧数 ÷ 本值，向上取整），内部循环与冷却仍按帧。
        self.frames_per_turn = (
            max(1, round(100.0 / self.timer_gain)) if self.timer_gain > 0 else 1
        )
        # 内部循环上限（帧）。max_rounds 以**回合**为单位，换算成帧给 while 用。
        self.max_frames = max(1, self.max_rounds * self.frames_per_turn)
        self.crit_mult = float(battle_cfg.get("crit_mult", 1.8))
        self.max_units = max(1, int(battle_cfg.get("max_units", 4)))
        # 等级战力：等级进入战斗公式（battle.level_power）。
        # **宠物等级 vs 怪物等级**直接对比——怪物等级 = 层号（见 tower._gen_monster）。
        # 入场时算一次并固定：局内升级不会立刻变强，避免刷级流。
        # run 在战斗入口是 dict（_do_battle 里 run = rogue.run_state），
        # 但 RogueRun 数据类也可能传进来，两种都要认。
        self.enemy_level = int(monster.get("level") or _state_floor(self.state))
        self.level_atk_mult, self.level_taken_mult = level_combat_mults(
            int(getattr(pet, "level", 1) or 1),
            self.enemy_level,
            cfg,
        )
        # 防御上限：单帧内最多结算多少次行动。正常情况下每帧最多
        # 「攒点 / 100」次，这个值只用于兜住配置异常（比如系数调到离谱）
        # 导致的死循环，不应该在正常数值下被触发。
        self.max_actions_per_frame = max(
            8, int(battle_cfg.get("max_actions_per_frame", 64))
        )
        # 群体技能（target: all）参数：每个目标的伤害衰减与命中上限。
        aoe_cfg = battle_cfg.get("aoe", {}) or {}
        self.aoe_factor = float(aoe_cfg.get("factor", 0.6))
        self.aoe_max_targets = max(
            1, int(aoe_cfg.get("max_targets", 0) or self.max_units)
        )
        # 「不可行动」（眩晕）参数：数值与上限全部外置，便于按首领特点配平。
        self.unactionable = unactionable_cfg(cfg)
        # 局内魔力：每层 / 每个战斗回合（= 我方每次行动）的回复比例。
        self.mp_floor_pct = mp_regen_pct(cfg, "per_floor_pct")
        self.mp_turn_pct = mp_regen_pct(cfg, "per_turn_pct")

        # 技能配置
        if not isinstance(monster.get("skills"), list | tuple):
            monster["skills"] = []
        self.skills_cfg = _load_skills_config()

        # 宠物属性计算
        self._compute_pet_stats()

        # 敌方配置
        self.enemy_skills_cfg: dict[str, Any] = cfg.get("enemy_skills", {}) or {}
        self.enemy_phases_cfg: dict[str, Any] = cfg.get("enemy_phases", {}) or {}
        # 精英专属机制：数值取自 elite_passives，单位上的 passive 字段只写 id。
        self.elite_passives_cfg: dict[str, Any] = cfg.get("elite_passives", {}) or {}
        self._setup_enemy_mp()

        # 战斗单位：群怪（monster["pack"]）入场即拆成多只**独立**单位，
        # 各自一条命、各攒各的攻击条——不是一个共用血条。
        # tower._split_pack 已把 hp/atk/def/spd 折算成「单只面板」，这里只负责
        # 复制出 count 份、按 ①②③ 命名，并把奖励留在第一只身上。
        pack = monster.get("pack") or {}
        pack_count = max(1, min(int(pack.get("count", 1) or 1), self.max_units))
        if pack_count > 1:
            # 编号从 ① 开始给**每一只**都加：如果第一只不带号，战报里会被
            # 读者当成「本体」，与「同质的 N 只」这个事实不符。
            first = dict(monster)
            first["name"] = f"{monster['name']}{_pack_mark(1)}"
            first["pack"] = {}
        else:
            first = monster
        self.units: list[dict[str, Any]] = [
            self._spawn_unit(first, primary=True, minion=False, pack=pack_count > 1)
        ]
        for index in range(2, pack_count + 1):
            member = dict(monster)
            member["hp"] = max(1, int(monster["hp"]))
            member["name"] = f"{monster['name']}{_pack_mark(index)}"
            # 掉落只结算一次（挂在第一只身上），否则打 3 只拿 3 份金币/经验。
            member["coins"] = 0
            member["exp"] = 0
            member["pack"] = {}
            self.units.append(
                self._spawn_unit(member, primary=False, minion=True, pack=True)
            )
        self.dots: list[dict[str, Any]] = []
        self.pet_debuffs: list[dict[str, Any]] = []
        self.pet_mods = {"atk_mult": 1.0, "def_mult": 1.0, "spd_mult": 1.0}
        self.current_target: dict[str, Any] | None = None
        # 敌方全员入场生命（群怪是全部成员之和）：战报的「敌方剩余 x/y」用它，
        # 不能用 monster["hp"]——那是拆分后的**单只**血量。
        self.enemy_hp_full = sum(max(0, int(rec["hp_cap"])) for rec in self.units)

        # 战斗状态
        self.gauge_pet = 0.0
        self.battle_timer = 0.0
        self.pet_acts = 0
        self.monster_acts = 0
        self.frames = 0
        """已经跑过的**内部帧**数（攻击条 / 冷却 / DoT 的结算单位）。

        注意：这不是「回合」。对外展示用 :attr:`turn`（帧数 ÷ frames_per_turn），
        否则 ATB 入场前几帧无人攒满、日志里就会从「回合2」甚至更晚开始。
        """
        self.crits = 0

    @property
    def turn(self) -> int:
        """当前**回合**（从 1 起）：内部帧数换算，计时条走满一次算一回合。

        第 1 回合覆盖第 1..frames_per_turn 帧，因此有行动的帧一定落在 ≥1 的回合里，
        不会出现「战报从回合2开始」这种观感。
        """
        return max(1, -(-self.frames // self.frames_per_turn))

    def _compute_pet_stats(self) -> None:
        """计算宠物入场属性。

        魔力（MP）是**局内跨层资源**：上限由 :func:`rogue_mp_max` 统一算出，
        当前值从 ``run["mp"]`` 取（入场满蓝、每层回复 ``battle.mp.per_floor_pct``）。
        """
        from ._common import passive_effects

        passives = passive_effects(self.pet, self.skills_cfg)
        attrs = self.pet.attributes
        buffs = self.state.get("buffs", {})
        relic_stats = relic_stat_bonuses(self.state.get("relics") or [])

        self.pet_hp = self.state["hp"]
        self.mp_cap = rogue_mp_max(
            self.pet,
            self.state.get("relics") or [],
            self.cfg,
            self.skills_cfg,
        )
        # 旧存档没有 run.mp：按「刚入场/刚回满」处理，并立即写回，避免每场重置。
        stored_mp = self.state.get("mp")
        if stored_mp is None:
            self.pet_mp = self.mp_cap
            self.state["mp"] = self.mp_cap
        else:
            self.pet_mp = max(0, min(self.mp_cap, int(stored_mp)))
        self.state["mp_max"] = self.mp_cap
        self.pet_atk = (
            int(
                (int(attrs.get("atk", 10)) + int(buffs.get("atk", 0)))
                * self.mults.get("atk_mult", 1.0)
            )
            + int(passives["atk"])
            + int(relic_stats["atk"])
        )
        self.pet_def = (
            int(
                (int(attrs.get("def", 10)) + int(buffs.get("def", 0)))
                * self.mults.get("def_mult", 1.0)
            )
            + int(passives["def"])
            + int(relic_stats["def"])
        )
        self.pet_spd = (
            int(
                (int(attrs.get("spd", 10)) + int(buffs.get("spd", 0)))
                * self.mults.get("spd_mult", 1.0)
            )
            + int(passives["spd"])
            + int(relic_stats["spd"])
        )
        self.hp_max = int(self.state.get("hp_max", self.pet_hp))
        self.crit_rate = float(passives["crit_rate"]) + float(relic_stats["crit_rate"])
        # 被动 dmg_mult 是平值相加的乘区（0.12 = +12%）；与遗物的 vs_boss_mult 相乘。
        self.dmg_mult = 1.0 + float(passives.get("dmg_mult", 0.0))
        self.out_mult = (
            self.mults.get("vs_boss_mult", 1.0)
            if (self.monster.get("boss") or self.monster.get("elite"))
            else 1.0
        )

        # 技能列表：只结算**装备中**的主动技能（收集池里没装备的不参与战斗）。
        self.active_pairs = equipped_specs(self.pet, "active", self.skills_cfg)
        self.cooldowns: dict[str, int] = {
            skill_id: 0 for skill_id, _ in self.active_pairs
        }

    def _setup_enemy_mp(self) -> None:
        """设置敌方蓝池参数。"""
        battle_cfg = self.cfg.get("battle", {})
        enemy_mp_cfg = battle_cfg.get("enemy_mp", {}) or {}
        self.mp_base = int(enemy_mp_cfg.get("base", 50))
        self.mp_regen = int(enemy_mp_cfg.get("regen", 8))
        if self.monster.get("boss"):
            self.primary_mp_mult = float(enemy_mp_cfg.get("boss_mult", 2.4))
        elif self.monster.get("elite"):
            self.primary_mp_mult = float(enemy_mp_cfg.get("elite_mult", 1.6))
        else:
            self.primary_mp_mult = 1.0
        self.minion_mp_mult = float(enemy_mp_cfg.get("minion_mult", 0.6))

    # -------------------------------------------------------------------------
    # 日志
    # -------------------------------------------------------------------------

    def _blog(self, key: str, default: str, **fields: Any) -> None:
        """记录战报。

        日志里的 ``{round}`` 是**对外回合**（:attr:`turn`），不是内部帧号——
        ATB 前几帧可能无人行动，用帧号会从「回合2」开始。
        """
        if self.log is None:
            return
        fields.setdefault("round", self.turn)
        self.log.append((self.turn, _text(key, default, **fields)))

    def _blog_crit(self, is_crit: bool) -> None:
        """暴击后缀。"""
        if self.log and is_crit:
            round_no, line = self.log[-1]
            self.log[-1] = (round_no, line + _text("log_crit", "（暴击）"))

    # -------------------------------------------------------------------------
    # 宠物侧
    # -------------------------------------------------------------------------

    def _pet_atk_eff(self) -> int:
        return max(1, round(self.pet_atk * self.pet_mods["atk_mult"]))

    def _pet_def_eff(self) -> int:
        return max(0, round(self.pet_def * self.pet_mods["def_mult"]))

    def _pet_spd_eff(self) -> int:
        return max(1, round(self.pet_spd * self.pet_mods["spd_mult"]))

    def _spd_gauge_gain(self, speed: float) -> float:
        """速度 → 每帧攻击条积攒量（**平方根曲线**，增长逐渐变慢）。

        ``gain = gauge_rate_mult × speed ** spd_exponent``

        * ``spd_exponent = 1.0``：线性（旧行为），速度翻倍则出手频率翻倍，
          高速度直接压过一切，越到后期越失控；
        * ``spd_exponent = 0.5``（默认）：开平方，速度 **翻 4 倍才让出手频率翻倍**。
          速度 12 → 22 → 44 → 88 的相对收益是 1 : 1.35 : 1.91 : 2.71，
          边际明显递减，但高速度仍然有意义，不会出现「堆到某个值就完全无用」。

        曲线只作用于「速度 → 攒条」这一步；速度的其它用途（出手排序、
        「不可行动」抵扣）仍用原始速度值。
        """
        if speed <= 0 or self.gauge_rate_mult <= 0:
            return 0.0
        return float(self.gauge_rate_mult) * float(speed) ** float(self.spd_exponent)

    def _pet_damage(self, defence: int, bossy: bool) -> tuple[int, bool]:
        """我方输出（等级战力 × 遗物 vs_boss_mult × 被动 dmg_mult × 好感加成 × 暴击）。

        ``level_atk_mult`` 是等级战力乘区（``battle.level_power``）：等级低于该层
        期望等级时 < 1（打不动），高于时 > 1。它乘在**基础伤害**上，与暴击、
        遗物乘区并列，所以练级是独立于装备的一条成长线。

        好感乘区最多 +10%。基础伤害**先按原口径 round**，好感加成叠在这个整数
        之上再 floor（``apply_affection_bonus``）——这样好感为 0 时伤害与改动前
        逐点相同，加成只对「已经确定的伤害」生效，不会顺带把基线压下去。
        """
        mult = (self.out_mult if bossy else 1.0) * self.dmg_mult * self.level_atk_mult
        base = max(
            1,
            round(
                _damage(
                    self._pet_atk_eff(),
                    defence,
                    self.variance,
                    self.def_softness,
                    self.rng,
                )
                * mult
            ),
        )
        dmg = max(1, apply_affection_bonus(base, int(self.pet.affection)))
        is_crit = self.crit_rate > 0 and self.rng.random() < self.crit_rate
        if is_crit:
            dmg = max(1, round(dmg * self.crit_mult))
            self.crits += 1
        return dmg, is_crit

    def _heal_amount(self, skill: dict[str, Any]) -> int:
        """治疗技能的回复量。"""
        if int(skill.get("power", 0)) != 0:
            return 0
        effects = skill.get("effects", []) or []
        return next(
            (int(e.get("amount", 0)) for e in effects if e.get("type") == "heal"), 0
        )

    @staticmethod
    def _is_aoe(skill: dict[str, Any]) -> bool:
        """是否群体技能（``target: all``）。

        群体技能一次打**全部存活敌人**，每个目标按 ``battle.aoe.factor`` 衰减。
        单体技能仍走 ``target: single``（默认）。``self`` 是治疗类，不算群攻。
        """
        return str(skill.get("target", "single")).strip().lower() in (
            "all",
            "all_enemies",
            "aoe",
        )

    def _skill_priority(self, skill_id: str, skill: dict[str, Any]) -> float:
        """技能排序权重（越大越先放）。

        在「威力 × 熟练度」之上叠加群体加成：群攻对**多目标**时总收益远高于
        单体，若仍按单体威力排序，玩家有群攻也永远轮不到它出手（单体威力
        普遍更高）。这里按「实际目标数」折算期望总伤害来决定优先级：

        * 单体：``威力``
        * 群体：``威力 × 命中目标数 × aoe_factor``（打 1 只时低于单体，
          所以只有真的一群怪在场时群攻才会被优先选中）
        """
        power = get_skill_power(self.pet, skill_id, self.skills_cfg)
        if not self._is_aoe(skill):
            return power
        hitting = max(1, min(len(self._live_units()), self.aoe_max_targets))
        return power * hitting * self.aoe_factor

    def _strike_back(self, unit: dict[str, Any], dealt: int) -> None:
        """棘刺特点：反弹伤害。"""
        thorns = float(unit.get("thorns", 0.0))
        if thorns > 0 and self.pet_hp > 0:
            rebound = max(0, round(dealt * thorns))
            self.pet_hp = max(1, self.pet_hp - rebound)
            self._blog("log_thorns", "回合{round} 棘刺反弹 {dmg} 点伤害。", dmg=rebound)

    def _regen_turn_mp(self) -> None:
        """回合回蓝：**战斗计时条走满一次**（= 一个回合结束）回复上限的 ``per_turn_pct``。

        触发点是「回合结束」而不是「我方行动」——一回合内双方可能各行动
        8~10 次（速度 20 × gauge_rate_mult 5 = 每帧满条），挂在行动上会变成
        「每次出手都回蓝」，战报里一回合出现好几条「魔力回复」。

        开局那一回合（计时条尚未走满）**不回复**：入场时魔力量就是上一层的
        结余，第一个回合只消耗不回补，回补发生在第 1 回合结束之后。
        """
        if self.mp_turn_pct <= 0 or self.mp_cap <= 0:
            return
        if self.pet_mp >= self.mp_cap:
            return
        gain = max(1, round(self.mp_cap * self.mp_turn_pct))
        before = self.pet_mp
        self.pet_mp = min(self.mp_cap, self.pet_mp + gain)
        gained = self.pet_mp - before
        if gained > 0:
            self._blog(
                "log_mp_turn",
                "回合{round} 魔力回复 {gain} 点（{mp}/{mp_max}）。",
                gain=gained,
                mp=self.pet_mp,
                mp_max=self.mp_cap,
            )

    def _pet_action(self) -> None:
        """宠物行动（一次出手，**不等于**一个回合）。"""
        self.gauge_pet -= 100.0
        self.pet_acts += 1
        target = self._pick_target()
        if target is None:
            return

        # 转向不写日志：一条「你的攻击转向 X」既占战报条数又不影响结果，
        # 目标变化从随后的伤害行就能看出来。
        self.current_target = target

        acted = False
        # 血量 < 50% 优先治疗
        if self.pet_hp < self.hp_max * 0.5:
            for skill_id, skill in self.active_pairs:
                if self.cooldowns.get(skill_id, 0) > 0:
                    continue
                amount = self._heal_amount(skill)
                cost = int(skill.get("mp_cost", 0))
                if amount and self.pet_mp >= cost:
                    self.pet_mp -= cost
                    self.pet_hp = min(self.hp_max, self.pet_hp + amount)
                    self.cooldowns[skill_id] = self._cooldown_frames(skill)
                    increase_proficiency(
                        self.pet, skill_id, skills_config=self.skills_cfg
                    )
                    acted = True
                    break

        # 否则按威力×熟练度从高到低放伤害技能
        if not acted:
            ranked = sorted(
                (
                    (sid, sk)
                    for sid, sk in self.active_pairs
                    if self._heal_amount(sk) == 0 and self.cooldowns.get(sid, 0) <= 0
                ),
                key=lambda pair: -self._skill_priority(pair[0], pair[1]),
            )
            for skill_id, skill in ranked:
                cost = int(skill.get("mp_cost", 0))
                if self.pet_mp < cost:
                    continue
                self.pet_mp -= cost
                is_aoe = self._is_aoe(skill)
                # 群体技能打**全部存活敌人**；单体技能打当前目标。
                targets = self._live_units() if is_aoe else [target]
                if not targets:
                    continue
                factor = (
                    get_skill_power(self.pet, skill_id, self.skills_cfg)
                    / BASE_SKILL_POWER
                )
                # 群体技能每个目标按 aoe_factor 衰减（见 roguelike.yaml battle.aoe）。
                if is_aoe:
                    factor *= self.aoe_factor
                strikes = max(1, int(skill.get("strikes", 1) or 1))
                dealt_total = 0
                crit_any = False
                dodged = 0
                for rec in targets:
                    for _ in range(strikes):
                        if rec["unit"]["hp"] <= 0:
                            break
                        # 闪避：每次命中独立判定，落空则本次不造成伤害
                        # （不计入打断累积、也不触发棘刺反弹）。
                        if self._dodged(rec):
                            dodged += 1
                            continue
                        dmg, is_crit = self._pet_damage(
                            int(rec["unit"]["def"]), bool(rec["primary"])
                        )
                        dealt = max(1, round(dmg * factor))
                        # 护盾先吃伤害；真正落到生命上的才计打断累积
                        # （打碎护盾不该等于「打疼了它」），而主伤害行仍报威力。
                        hp_dealt = self._absorb_shield(rec, dealt)
                        rec["unit"]["hp"] -= hp_dealt
                        dealt_total += dealt
                        crit_any = crit_any or is_crit
                        self._charge_taken(rec, hp_dealt)
                        self._strike_back(rec["unit"], dealt)
                        self._unit_rage(rec)
                if dealt_total <= 0 and dodged > 0:
                    # 全部落空：单独一条，避免出现「造成 0 伤害」这种怪句子。
                    self._blog(
                        "log_dodged_all",
                        "回合{round} {actor} 使用【{skill}】，但被对手闪开了。",
                        actor=self.pet.name,
                        skill=str(skill.get("name") or skill_id),
                    )
                elif is_aoe:
                    self._blog(
                        "log_skill_aoe",
                        "回合{round} {actor} 使用【{skill}】，"
                        "对 {count} 名敌人共造成 {dmg} 伤害。",
                        actor=self.pet.name,
                        skill=str(skill.get("name") or skill_id),
                        count=len(targets),
                        dmg=dealt_total,
                    )
                elif strikes > 1:
                    self._blog(
                        "log_skill_multi",
                        "回合{round} {actor} 使用【{skill}】，{strikes} 连击共 {dmg} 伤害。",
                        actor=self.pet.name,
                        skill=str(skill.get("name") or skill_id),
                        strikes=strikes,
                        dmg=dealt_total,
                    )
                else:
                    self._blog(
                        "log_skill",
                        "回合{round} {actor} 使用【{skill}】，造成 {dmg} 伤害。",
                        actor=self.pet.name,
                        skill=str(skill.get("name") or skill_id),
                        dmg=dealt_total,
                    )
                self._blog_crit(crit_any)
                # 注意：打断累积与棘刺反弹已在逐目标循环里结算过，
                # 这里不能再对 target 重复调用（否则单体技能会双倍反弹/双倍打断）。
                # 吸血：按实际伤害比例回复局内生命（不超上限）。
                leech = float(skill.get("leech", 0.0) or 0.0)
                if leech > 0 and dealt_total > 0:
                    healed = min(
                        round(dealt_total * leech), max(0, self.hp_max - self.pet_hp)
                    )
                    if healed > 0:
                        self.pet_hp += healed
                        self._blog(
                            "log_skill_leech",
                            "回合{round} {actor} 吸取 {heal} 点生命。",
                            actor=self.pet.name,
                            heal=healed,
                        )
                # 回蓝效果
                for effect in skill.get("effects", []) or []:
                    if effect.get("type") == "mp":
                        self.pet_mp = min(
                            self.mp_cap, self.pet_mp + int(effect.get("amount", 0))
                        )
                self.cooldowns[skill_id] = self._cooldown_frames(skill)
                increase_proficiency(self.pet, skill_id, skills_config=self.skills_cfg)
                acted = True
                break

        # 普攻
        if not acted:
            if self._dodged(target):
                self._blog(
                    "log_attack_dodged",
                    "回合{round} {actor} 普攻，被对手闪开了。",
                    actor=self.pet.name,
                )
            else:
                dmg, is_crit = self._pet_damage(
                    int(target["unit"]["def"]), bool(target["primary"])
                )
                hp_dealt = self._absorb_shield(target, dmg)
                target["unit"]["hp"] -= hp_dealt
                self._blog(
                    "log_attack",
                    "回合{round} {actor} 普攻，造成 {dmg} 伤害。",
                    actor=self.pet.name,
                    dmg=dmg,
                )
                self._blog_crit(is_crit)
                self._charge_taken(target, hp_dealt)
                self._strike_back(target["unit"], dmg)
                self._unit_rage(target)

        self._settle_deaths()

    # -------------------------------------------------------------------------
    # 敌方侧
    # -------------------------------------------------------------------------

    def _dodged(self, rec: dict[str, Any]) -> bool:
        """目标是否闪开了这一次命中（``monster_traits`` 的 ``dodge``）。

        每次命中独立判定，与暴击判定互不影响。闪避成功时本次不造成伤害，
        也不计打断累积、不触发棘刺反弹——它根本没打到。
        """
        rate = float(rec["unit"].get("dodge", 0.0) or 0.0)
        return rate > 0 and self.rng.random() < rate

    def _cooldown_frames(self, skill: dict[str, Any]) -> int:
        """技能的冷却时长（换算成**帧**）。

        配置里的 ``cooldown`` 单位仍是一个**回合**（= ``frames_per_turn`` 帧），
        但内部记的是剩余帧数，每帧 -1——这样冷却只取决于释放时刻，
        不会因为「刚好跨过回合边界」而白送一次 -1。
        """
        return int(skill.get("cooldown", 0) or 0) * self.frames_per_turn

    def _skill_label(self, skill: dict[str, Any], skill_id: str) -> str:
        return str(skill.get("name") or skill_id)

    def _unit_skills(self, unit: dict[str, Any]) -> list[str]:
        return [
            str(sid)
            for sid in (unit.get("skills") or [])
            if str(sid) in self.enemy_skills_cfg
        ]

    def _spawn_unit(
        self,
        unit: dict[str, Any],
        *,
        primary: bool,
        minion: bool,
        pack: bool = False,
    ) -> dict[str, Any]:
        """生成战斗单位。

        ``pack`` 仅标记「这只属于群怪」（同一群里的独立个体）；它与 ``minion``
        的区别是语义：召唤物在本体倒下时一并消散，群怪成员不会。
        """
        skills = self._unit_skills(unit)
        mult = self.minion_mp_mult if minion else self.primary_mp_mult
        pool = max(0, int(self.mp_base * mult))
        record = {
            "unit": unit,
            "primary": primary,
            "minion": minion,
            "pack": pack,
            "hp_cap": max(1, int(unit["hp"])),
            "skills": skills,
            "cd": dict.fromkeys(skills, 0),
            "mp": pool,
            "mp_cap": pool,
            "gauge": 0.0,
            "charge": None,
            # 「不可行动」值（速度点）：每轮先用速度抵扣，剩余速度才攒行动条。
            "stun": 0.0,
            "stun_total": 0.0,
            # ↑ 本场战斗**累计**吃到的控制回合数（只增不减）。用作
            #   battle.unactionable.stack_limit_rounds 的预算：一场里同一单位最多
            #   被控制这么多回合。挣脱后不清零——否则反复打断首领就能无限续控。
            "phases": [
                str(pid)
                for pid in (unit.get("phases") or [])
                if str(pid) in self.enemy_phases_cfg
            ],
            "fired": set(),
        }
        self._init_passive(record)
        return record

    def _init_passive(self, record: dict[str, Any]) -> None:
        """套用精英专属机制（``elite_passives``）。

        ``unit["passive"]`` 只存 id，数值从配置读——这样同一套机制换区域、
        换精英都只改一个 id，不用逐只抄一遍数字。id 写错时**静默忽略**：
        一个手误不该让整场战斗炸掉或变成无机制（只是退化）。

        护盾在入场就落地（``record["shield"]``），其余机制在各自时机触发：
        受击叠怒在每次被命中、抽蓝在开场与回合结束、蚀骨在它的每次行动。
        """
        passive_id = str(record["unit"].get("passive") or "")
        spec = self.elite_passives_cfg.get(passive_id)
        if not isinstance(spec, dict):
            record["passive"] = ""
            return
        record["passive"] = passive_id
        if passive_id == "bulwark":
            record["shield"] = max(
                1, round(int(record["hp_cap"]) * float(spec.get("pct", 0.0)))
            )
        elif passive_id == "rage":
            record["rage_per_stack"] = float(spec.get("atk_per_stack", 0.0) or 0.0)
            record["rage_max"] = max(0, int(spec.get("max_stacks", 0) or 0))
            record["rage"] = 0
        elif passive_id == "siphon":
            record["siphon_pct"] = float(spec.get("pct", 0.0) or 0.0)
            record["siphon_min"] = max(0, int(spec.get("min", 0) or 0))
        elif passive_id == "erode":
            record["erode_per_stack"] = float(spec.get("def_per_stack", 0.0) or 0.0)
            record["erode_max"] = max(0, int(spec.get("max_stacks", 0) or 0))
            record["erode_stacks"] = 0

    @staticmethod
    def _is_pack_member(record: dict[str, Any]) -> bool:
        """该单位是否属于群怪（同群的独立个体，不随本体倒下而消散）。"""
        return bool(record.get("pack"))

    def _live_units(self) -> list[dict[str, Any]]:
        return [rec for rec in self.units if rec["unit"]["hp"] > 0]

    def _pick_target(self) -> dict[str, Any] | None:
        """自动选目标：血量比例最低的存活敌人。"""
        alive = self._live_units()
        if not alive:
            return None
        return min(
            alive,
            key=lambda rec: (
                rec["unit"]["hp"] / max(1, rec["hp_cap"]),
                0 if rec["primary"] else 1,
            ),
        )

    def _settle_deaths(self) -> None:
        """击倒结算。

        「本体倒下，召唤物一同消散」只适用于**召唤**出来的援军；群怪
        （``monster["pack"]``）是各自独立的个体，打掉一只绝不能连带清场——
        它们本来就不是召唤物，只是同名的同类。
        """
        primary = self.units[0]
        if primary["unit"]["hp"] <= 0:
            for record in self.units:
                if record is primary or self._is_pack_member(record):
                    continue
                record["charge"] = None
                if record["unit"]["hp"] > 0:
                    record["unit"]["hp"] = 0
        for record in self.units:
            if record["unit"]["hp"] <= 0 and not record.get("dead_logged"):
                record["dead_logged"] = True
                record["charge"] = None
                self._blog(
                    "log_slain",
                    "回合{round} {name} 被击倒。",
                    name=str(record["unit"]["name"]),
                )
                self._death_burst(record)

    def _unit_regen(self) -> None:
        """敌方每回合再生（``monster_traits`` 的 ``regen_pct``）。

        回复量 = **自身生命上限** × ``regen_pct``（不是当前生命，避免低血时
        回复量骤减到没有意义），封顶上限。按回合结算，不按出手次数。
        """
        for rec in self._live_units():
            pct = float(rec["unit"].get("regen_pct", 0.0) or 0.0)
            if pct <= 0:
                continue
            unit = rec["unit"]
            heal = min(
                max(0, round(int(rec["hp_cap"]) * pct)),
                max(0, int(rec["hp_cap"]) - int(unit["hp"])),
            )
            if heal <= 0:
                continue
            unit["hp"] += heal
            self._blog(
                "log_enemy_regen",
                "回合{round} {name} 正在愈合，回复 {heal} 点生命。",
                name=str(unit["name"]),
                heal=heal,
            )

    def _death_burst(self, rec: dict[str, Any]) -> None:
        """死亡爆炸（``monster_traits`` 的 ``death_burst``）。

        被击倒时对我方造成 ``自身攻击 × death_burst`` 的伤害。**不致死**：
        最多把我方压到 1 血，与 DoT / 棘刺同一口径——否则「赢了却被尸体炸死」
        会让战斗结果变得不可理解。
        """
        ratio = float(rec["unit"].get("death_burst", 0.0) or 0.0)
        if ratio <= 0 or self.pet_hp <= 0:
            return
        dmg = max(1, round(int(rec["unit"]["atk"]) * ratio))
        self.pet_hp = max(1, self.pet_hp - dmg)
        self._blog(
            "log_death_burst",
            "回合{round} {name} 倒下时炸开，造成 {dmg} 伤害（你剩余 {hp}）。",
            name=str(rec["unit"]["name"]),
            dmg=dmg,
            hp=self.pet_hp,
        )

    # -------------------------------------------------------------------------
    # 精英专属机制（elite_passives）
    # -------------------------------------------------------------------------
    #
    # 四个机制的共同原则：**每一步都留痕**。它们存在的意义不是「偷偷变强」，
    # 而是让玩家在战报里看到「护盾碎了 / 它被激怒了 / 蓝被抽走 / 护甲被腐蚀」，
    # 从而复盘这场为什么打不过——所以每次触发都单独播报一行。

    def _absorb_shield(self, rec: dict[str, Any], dealt: int) -> int:
        """护盾吸收（``bulwark``），返回**真正落在生命上**的伤害。

        护盾不吃治疗、不会回补，只按入场值的上限单向消耗。战报里主伤害行仍报
        **命中威力**（``dealt``），差额由护盾行解释——否则满盾吸收会显示成
        「造成 0 伤害」，看起来像攻击失效。
        """
        shield = int(rec.get("shield", 0) or 0)
        if shield <= 0 or dealt <= 0:
            return dealt
        absorbed = min(shield, dealt)
        rec["shield"] = shield - absorbed
        if rec["shield"] <= 0 and not rec.get("shield_broken"):
            rec["shield_broken"] = True
            self._blog(
                "log_passive_shield_break",
                "回合{round} {name} 的护盾碎裂了！",
                name=str(rec["unit"]["name"]),
            )
        return dealt - absorbed

    def _unit_rage(self, rec: dict[str, Any]) -> None:
        """受击叠怒（``rage``）：每次被命中 +1 层，攻击按层数线性提高。

        只有**真的被打到**才叠：闪避掉的那次不算。封顶 ``max_stacks``，
        所以最多 ``atk_per_stack × max_stacks`` 的加成。
        """
        per = float(rec.get("rage_per_stack", 0.0) or 0.0)
        cap = int(rec.get("rage_max", 0) or 0)
        if per <= 0 or cap <= 0:
            return
        stacks = int(rec.get("rage", 0) or 0)
        if stacks >= cap:
            return
        stacks += 1
        rec["rage"] = stacks
        # 只在第 1 层与封顶层播报：中间层静默。否则多段/群攻技能会在一场
        # 短战斗里刷出 5 条「被激怒」，把真正决定胜负的行挤出 10 条战报窗口。
        if stacks != 1 and stacks < cap:
            return
        self._blog(
            "log_passive_rage",
            "回合{round} {name} 被激怒了（攻击 +{pct}%，{stacks}/{cap} 层）。",
            name=str(rec["unit"]["name"]),
            pct=round(per * stacks * 100),
            stacks=stacks,
            cap=cap,
        )

    def _passive_siphon(self) -> None:
        """抽蓝（``siphon``）：抽走我方一部分魔力，并转成自身魔力。

        触发两次：**开场一次** + 之后每个回合结束各一次。开场那次是刻意的
        ——精英战通常只有 1 个回合（ATB 一回合 10 帧，双方各出手 4~6 次），
        只挂在「回合结束」上的话，一场没跑满 10 帧的战斗里这条机制完全不出现，
        玩家根本看不到「这只在抽我的蓝」。

        抽多少 = 上限 × ``pct``，且不低于 ``min``；自己有多少就抽多少，
        所以 0 蓝时不会有任何播报（避免空刷一条）。
        """
        if self.pet_mp <= 0:
            return
        for rec in self._live_units():
            pct = float(rec.get("siphon_pct", 0.0) or 0.0)
            if pct <= 0:
                continue
            want = max(int(rec.get("siphon_min", 0) or 0), round(self.mp_cap * pct))
            steal = min(self.pet_mp, max(1, int(want)))
            if steal <= 0:
                continue
            self.pet_mp -= steal
            rec["mp"] = min(rec["mp_cap"], rec["mp"] + steal)
            self._blog(
                "log_passive_siphon",
                "回合{round} {name} 抽走了你 {amount} 点魔力（剩余 {mp}）。",
                name=str(rec["unit"]["name"]),
                amount=steal,
                mp=self.pet_mp,
            )

    def _passive_erode(self, rec: dict[str, Any]) -> None:
        """蚀骨（``erode``）：它**每次行动**都腐蚀我方护甲一层，层层叠加。

        挂在「它的行动」而不是「回合结束」上：精英战往往跑不满一个回合，
        按回合结算的话这条机制大多数战斗都不会出现。按行动结算还更符合直觉
        ——「它的每一击都带着腐蚀」。

        复用 ``self.pet_debuffs`` 的**乘区**机制：每叠一层追加一条
        ``def_mult = 1 - def_per_stack`` 的常驻条目，``_refresh_pet_mods()``
        会把它们连乘起来，所以不需要另写一套「叠加层数」的状态。

        每层 ``frames`` 给一个极大值——它**不该自然消退**，封顶只由
        ``max_stacks`` 决定。播报只在第 1 层与封顶层，中间层静默：
        否则一场短战斗的战报会被几条同样的「防御下降」挤满。
        """
        per = float(rec.get("erode_per_stack", 0.0) or 0.0)
        cap = int(rec.get("erode_max", 0) or 0)
        if per <= 0 or cap <= 0:
            return
        stacks = int(rec.get("erode_stacks", 0) or 0)
        if stacks >= cap:
            return
        stacks += 1
        rec["erode_stacks"] = stacks
        self.pet_debuffs.append(
            {
                "id": f"passive:erode:{id(rec)}",
                "frames": 10**9,
                "rounds": 0,
                "def_mult": max(0.0, 1.0 - per),
            }
        )
        self._refresh_pet_mods()
        if stacks == 1 or stacks >= cap:
            # 报**累计**跌幅而不是层数 × 单层：连乘 0.85³ 实际是 -38.6%，
            # 按 3 × 15% 报 -45% 会让玩家对不上伤害表。
            lost = 1.0 - (1.0 - per) ** stacks
            self._blog(
                "log_passive_erode",
                "回合{round} {name} 的腐蚀爬上你的护甲（防御 -{pct}%，{stacks}/{cap} 层）。",
                name=str(rec["unit"]["name"]),
                pct=round(lost * 100),
                stacks=stacks,
                cap=cap,
            )

    def _unit_leech(
        self, rec: dict[str, Any], dealt: int, ratio: float | None = None
    ) -> int:
        """吸血。"""
        unit = rec["unit"]
        if ratio is None:
            ratio = float(unit.get("leech", 0.0))
        ratio = float(ratio)
        if ratio <= 0 or dealt <= 0:
            return 0
        heal = min(round(dealt * ratio), max(0, rec["hp_cap"] - int(unit["hp"])))
        if heal <= 0:
            return 0
        unit["hp"] += heal
        return heal

    def _charge_taken(self, rec: dict[str, Any], dealt: int) -> None:
        """打断判定：累积伤害达到阈值即打断，并施加「不可行动」。"""
        charge = rec["charge"]
        if charge is None or dealt <= 0:
            return
        charge["taken"] += dealt
        if charge["threshold"] <= 0 or charge["taken"] < charge["threshold"]:
            return
        skill_id = charge["skill_id"]
        skill = self.enemy_skills_cfg.get(skill_id) or {}
        rec["charge"] = None
        rec["cd"][skill_id] = self._cooldown_frames(skill)
        self._blog(
            "log_interrupt",
            "回合{round} {actor} 的【{skill}】被打断了！",
            actor=rec["unit"]["name"],
            skill=self._skill_label(skill, skill_id),
        )
        self._apply_unactionable(rec, skill, charge)

    def _apply_unactionable(
        self,
        rec: dict[str, Any],
        skill: dict[str, Any],
        charge: dict[str, Any] | None = None,
    ) -> None:
        """蓄力被打断 → 施加「不可行动」值（单位：速度点）。

        取值：技能自身 ``unactionable``（回合数）→ ``battle.unactionable`` 默认值；
        蓄力越久惩罚越重（``charge_bonus``）；再按 ``max_rounds`` 与
        ``stack_limit_rounds`` 双重封顶、按 ``boss_resist`` 打折。

        速度点 = 目标当前速度 × 回合数，因此高速首领被扣得更多，节奏按比例回到
        与低速单位一致——不会出现「速度越高越容易被永久锁住」。
        """
        cfg = self.unactionable
        if not cfg.get("enabled", True):
            return
        base = float(
            skill.get("unactionable", cfg.get("default_rounds", 1.0))
            or cfg.get("default_rounds", 1.0)
        )
        if base <= 0:
            return
        # 蓄力越久，被打断的代价越高（charge 2 → +0.5 回合）。
        charge_rounds = int(skill.get("charge", 0) or 0)
        bonus = max(0, charge_rounds - 1) * float(cfg.get("charge_bonus", 0.0) or 0.0)
        rounds = base + bonus
        rounds = min(rounds, float(cfg.get("max_rounds", 2.5) or rounds))
        if rec["unit"].get("boss"):
            rounds *= float(cfg.get("boss_resist", 1.0) or 1.0)
        # 连控上限：同一单位在「不可行动」期间的再次打断不无限叠加。
        spent = float(rec.get("stun_total", 0.0))
        cap = float(cfg.get("stack_limit_rounds", 4.0) or 0.0)
        if cap > 0:
            rounds = min(rounds, max(0.0, cap - spent))
        if rounds <= 0:
            return
        gain = max(1.0, float(rec["unit"]["spd"]) * rounds)
        rec["stun"] = float(rec.get("stun", 0.0)) + gain
        rec["stun_total"] = spent + rounds
        # 打断同时清空已攒的行动条，并取消它**本轮**尚未执行的那次行动：
        # 只清行动条不够——它本轮的行动早在轮初就排进行动队列了，
        # 不拦下来的话「打断」当轮照常挨打，控制等于没生效。
        if cfg.get("reset_gauge", True):
            rec["gauge"] = 0.0
            rec["skip_round"] = True

    def _unit_phases(self, rec: dict[str, Any]) -> None:
        """形态触发。"""
        unit = rec["unit"]
        if not rec["phases"] or unit["hp"] <= 0:
            return
        for phase_id in rec["phases"]:
            if phase_id in rec["fired"]:
                continue
            phase = self.enemy_phases_cfg.get(phase_id) or {}
            if unit["hp"] / rec["hp_cap"] > float(phase.get("at_hp", 0.0)):
                continue
            rec["fired"].add(phase_id)
            for stat in ("atk", "def", "spd"):
                mult = float(phase.get(f"{stat}_mult", 1.0))
                if mult != 1.0:
                    unit[stat] = max(1, round(int(unit[stat]) * mult))
            healed = min(
                round(rec["hp_cap"] * float(phase.get("heal", 0.0))),
                max(0, rec["hp_cap"] - int(unit["hp"])),
            )
            if healed > 0:
                unit["hp"] += healed
            for skill_id in phase.get("add_skills") or []:
                skill_id = str(skill_id)
                if skill_id in self.enemy_skills_cfg and skill_id not in rec["skills"]:
                    rec["skills"].append(skill_id)
                    rec["cd"].setdefault(skill_id, 0)
            label = str(phase.get("name") or phase_id)
            if healed > 0:
                self._blog(
                    "log_phase_heal",
                    "回合{round} {actor} 进入形态【{phase}】，回复 {heal} 点生命！",
                    actor=unit["name"],
                    phase=label,
                    heal=healed,
                )
            else:
                self._blog(
                    "log_phase",
                    "回合{round} {actor} 进入形态【{phase}】！",
                    actor=unit["name"],
                    phase=label,
                )

    def _pick_enemy_skill(
        self, rec: dict[str, Any]
    ) -> tuple[str, dict[str, Any]] | None:
        """按 priority 选技能。"""
        candidates = []
        for skill_id in rec["skills"]:
            skill = self.enemy_skills_cfg[skill_id]
            if rec["cd"].get(skill_id, 0) > 0:
                continue
            if int(skill.get("mp_cost", 0)) > rec["mp"]:
                continue
            candidates.append(
                (
                    float(skill.get("priority", 0)),
                    int(skill.get("power", 0)),
                    skill_id,
                    skill,
                )
            )
        if not candidates:
            return None
        candidates.sort(key=lambda item: (-item[0], -item[1]))
        return candidates[0][2], candidates[0][3]

    def _enemy_summon(self, rec: dict[str, Any], spec: dict[str, Any]) -> None:
        """召唤。"""
        unit = rec["unit"]
        name = str(spec.get("name") or "召唤物")
        room = self.max_units - len(self._live_units())
        if room <= 0:
            self._blog(
                "log_summon_full",
                "回合{round} {actor} 想召唤 {name}，但场上已满。",
                actor=unit["name"],
                name=name,
            )
            return
        count = min(max(1, int(spec.get("count", 1))), room)
        for _ in range(count):
            minion = {
                "name": name,
                "desc": str(spec.get("desc", "")),
                "hp": max(1, round(rec["hp_cap"] * float(spec.get("hp_mult", 0.05)))),
                "atk": max(
                    1, round(int(unit["atk"]) * float(spec.get("atk_mult", 0.5)))
                ),
                "def": max(
                    0, round(int(unit["def"]) * float(spec.get("def_mult", 0.5)))
                ),
                "spd": max(
                    1, round(int(unit["spd"]) * float(spec.get("spd_mult", 1.0)))
                ),
                "coins": 0,
                "exp": 0,
                "boss": False,
                "elite": False,
                "traits": [],
                "skills": [str(s) for s in (spec.get("skills") or [])],
                "phases": [],
            }
            self.units.append(self._spawn_unit(minion, primary=False, minion=True))
        self._blog(
            "log_summon",
            "回合{round} {actor} 召唤了 {count} 只 {name}。",
            actor=unit["name"],
            count=count,
            name=name,
        )

    def _enemy_skill(
        self,
        rec: dict[str, Any],
        skill_id: str,
        skill: dict[str, Any],
        *,
        charged: bool = False,
    ) -> None:
        """释放敌方技能。"""
        unit = rec["unit"]
        label = self._skill_label(skill, skill_id)
        power = int(skill.get("power", 0))
        strikes = max(1, int(skill.get("strikes", 1)))
        rate = float(skill.get("crit_rate", 0.0))
        dealt = 0

        if power > 0:
            for _ in range(strikes):
                if self.pet_hp <= 0 or unit["hp"] <= 0:
                    break
                dmg = self._enemy_damage_to_pet(
                    int(unit["atk"]),
                    multiplier=power / BASE_SKILL_POWER,
                    crit_rate=rate,
                    rec=rec,
                )
                self.pet_hp -= dmg
                dealt += dmg

            if charged:
                self._blog(
                    "log_charge_hit",
                    "回合{round} {actor} 释放【{skill}】，造成 {dmg} 伤害！",
                    actor=unit["name"],
                    skill=label,
                    dmg=dealt,
                )
            elif strikes > 1:
                self._blog(
                    "log_skill_hit",
                    "回合{round} {actor} 使用【{skill}】，{strikes} 连击共 {dmg} 伤害。",
                    actor=unit["name"],
                    skill=label,
                    strikes=strikes,
                    dmg=dealt,
                )
            else:
                self._blog(
                    "log_skill",
                    "回合{round} {actor} 使用【{skill}】，造成 {dmg} 伤害。",
                    actor=unit["name"],
                    skill=label,
                    dmg=dealt,
                )

        leeched = self._unit_leech(rec, dealt, float(skill.get("leech", 0.0)) or None)
        if leeched > 0:
            self._blog(
                "log_leech",
                "回合{round} {actor} 吸血回复 {heal} 点。",
                actor=unit["name"],
                heal=leeched,
            )

        heal_frac = float(skill.get("heal", 0.0))
        if heal_frac > 0:
            healed = min(
                round(rec["hp_cap"] * heal_frac),
                max(0, rec["hp_cap"] - int(unit["hp"])),
            )
            if healed > 0:
                unit["hp"] += healed
                self._blog(
                    "log_skill_heal",
                    "回合{round} {actor} 使用【{skill}】，回复 {heal} 点生命。",
                    actor=unit["name"],
                    skill=label,
                    heal=healed,
                )

        buff = skill.get("self_buff") or {}
        if buff:
            for stat in ("atk", "def", "spd"):
                mult = float(buff.get(f"{stat}_mult", 1.0))
                if mult != 1.0:
                    unit[stat] = max(1, round(int(unit[stat]) * mult))
            self._blog(
                "log_skill_buff",
                "回合{round} {actor} 使用【{skill}】，自身强化。",
                actor=unit["name"],
                skill=label,
            )

        debuff = skill.get("debuff") or {}
        if debuff:
            rounds = max(1, int(debuff.get("rounds", 1)))
            entry: dict[str, Any] = {
                "id": skill_id,
                # frames = 剩余帧数。用 frames 而不是「回合末 -1」，
                # 才能让持续时间只取决于施加时刻，与回合边界无关。
                "frames": rounds * self.frames_per_turn,
                "rounds": rounds,
            }
            for stat in ("atk", "def", "spd"):
                entry[f"{stat}_mult"] = float(debuff.get(f"{stat}_mult", 1.0))
            self.pet_debuffs[:] = [e for e in self.pet_debuffs if e["id"] != skill_id]
            self.pet_debuffs.append(entry)
            self._refresh_pet_mods()
            self._blog(
                "log_skill_debuff",
                "回合{round} {actor} 使用【{skill}】，你的状态被削弱。",
                actor=unit["name"],
                skill=label,
            )

        dot = skill.get("dot") or {}
        if dot:
            ticks = max(1, int(dot.get("rounds", 1)))
            self.dots.append(
                {
                    "atk": int(unit["atk"]),
                    "frac": float(dot.get("frac", 0.0)),
                    # 第一次结算在一个回合之后，之后每满一个回合结算一次。
                    "frames": self.frames_per_turn,
                    "ticks": ticks,
                    "rounds": ticks,
                }
            )
            self._blog(
                "log_skill_dot",
                "回合{round} {actor} 使用【{skill}】，你陷入持续伤害。",
                actor=unit["name"],
                skill=label,
            )

        spec = skill.get("summon")
        if spec:
            self._enemy_summon(rec, spec)

    def _enemy_damage_to_pet(
        self,
        atk: int,
        *,
        multiplier: float = 1.0,
        crit_rate: float = 0.0,
        rec: dict[str, Any] | None = None,
    ) -> int:
        """敌方打到我方的单次伤害（统一口径，含等级承伤乘区）。

        敌方普攻与技能都走这里：基础伤害 → 技能/多段威力系数 → 暴击 →
        **等级承伤乘区**（``level_taken_mult``，等级低于该层期望等级时 > 1，
        即挨得更痛）→ **受击叠怒**（``rage`` 层数加成）。集中一处避免两条
        路径的乘区口径分叉。
        """
        dmg = _damage(
            int(atk),
            self._pet_def_eff(),
            self.variance,
            self.def_softness,
            self.rng,
        )
        dmg = max(1, round(dmg * float(multiplier)))
        if crit_rate > 0 and self.rng.random() < crit_rate:
            dmg = max(1, round(dmg * self.crit_mult))
        dmg = max(1, round(dmg * self.level_taken_mult))
        if rec is not None:
            stacks = int(rec.get("rage", 0) or 0)
            per = float(rec.get("rage_per_stack", 0.0) or 0.0)
            if stacks > 0 and per > 0:
                dmg = max(1, round(dmg * (1.0 + stacks * per)))
        return dmg

    def _enemy_plain(self, rec: dict[str, Any]) -> None:
        """普攻。"""
        unit = rec["unit"]
        strikes = max(1, int(unit.get("strikes", 1)))
        strike_mult = float(unit.get("strike_mult", 1.0))
        rate = float(unit.get("crit_rate", 0.0))
        dealt = 0
        for _ in range(strikes):
            if self.pet_hp <= 0 or unit["hp"] <= 0:
                break
            dmg = self._enemy_damage_to_pet(
                int(unit["atk"]), multiplier=strike_mult, crit_rate=rate, rec=rec
            )
            self.pet_hp -= dmg
            dealt += dmg
        self._unit_leech(rec, dealt)
        self._blog(
            "log_attack",
            "回合{round} {actor} 普攻，造成 {dmg} 伤害。",
            actor=unit["name"],
            dmg=dealt,
        )

    def _enemy_action(self, rec: dict[str, Any]) -> None:
        """敌方行动。"""
        # 「蚀骨」挂在它的**每次行动**上（含蓄力那一次）：一次出手剥一层护甲。
        self._passive_erode(rec)
        charge = rec["charge"]
        if charge is not None:
            charge["left"] -= 1
            if charge["left"] > 0:
                return
            skill_id = charge["skill_id"]
            skill = self.enemy_skills_cfg.get(skill_id) or {}
            rec["charge"] = None
            self._blog(
                "log_charge_ready",
                "回合{round} {actor} 蓄力完成【{skill}】，即将释放！",
                actor=rec["unit"]["name"],
                skill=self._skill_label(skill, skill_id),
            )
            self._enemy_skill(rec, skill_id, skill, charged=True)
            self._unit_phases(rec)
            return

        picked = self._pick_enemy_skill(rec)
        if picked is None:
            self._enemy_plain(rec)
            return

        skill_id, skill = picked
        rec["mp"] = max(0, rec["mp"] - int(skill.get("mp_cost", 0)))
        rec["cd"][skill_id] = self._cooldown_frames(skill)
        charge_rounds = int(skill.get("charge", 0))
        if charge_rounds > 0:
            rec["charge"] = {
                "skill_id": skill_id,
                "left": max(0, charge_rounds - 1),
                "taken": 0,
                "threshold": rec["hp_cap"] * float(skill.get("interrupt", 0.0)),
            }
            self._blog(
                "log_charge_start",
                "回合{round} {actor} 开始蓄力【{skill}】——打断它！",
                actor=rec["unit"]["name"],
                skill=self._skill_label(skill, skill_id),
            )
            return

        self._enemy_skill(rec, skill_id, skill)
        self._unit_phases(rec)

    # -------------------------------------------------------------------------
    # 削弱与持续伤害
    # -------------------------------------------------------------------------

    def _refresh_pet_mods(self) -> None:
        for key in self.pet_mods:
            value = 1.0
            for entry in self.pet_debuffs:
                value *= float(entry.get(key, 1.0))
            self.pet_mods[key] = value

    # -------------------------------------------------------------------------
    # 主循环
    # -------------------------------------------------------------------------

    def run(self) -> BattleResult:
        """运行战斗直到结束。

        循环与 ``max_rounds`` 上限按**内部帧**计（`battle.max_frames`），
        ``BattleResult.rounds`` 回报的是**对外回合**。
        """
        # 战斗开场：先结算一次「抽蓝」。精英战常常跑不满一个回合，只挂在
        # 回合结束上的话这条机制在短战斗里完全不出现（见 _passive_siphon）。
        self._passive_siphon()
        while (
            self.frames < self.max_frames
            and self.pet_hp > 0
            and self._pick_target() is not None
        ):
            self.frames += 1
            self._run_round()

        victory = self._pick_target() is None and self.pet_hp > 0
        if (
            not victory
            and self.pet_hp > 0
            and self._pick_target() is not None
            and self.frames >= self.max_frames
        ):
            self._blog(
                "log_over",
                "回合{round} 战斗在第 {max} 回合上限处中止。",
                max=self.max_rounds,
            )

        return BattleResult(
            pet_hp=self.pet_hp,
            pet_mp=self.pet_mp,
            victory=victory,
            rounds=self.turn,
            pet_acts=self.pet_acts,
            monster_acts=self.monster_acts,
            crits=self.crits,
            mp_cap=self.mp_cap,
            enemy_hp=sum(max(0, int(rec["unit"]["hp"])) for rec in self.units),
            enemy_hp_full=self.enemy_hp_full,
            enemy_units=len(self.units),
            enemy_alive=len(self._live_units()),
        )

    def _run_round(self) -> None:
        """执行一轮（帧）。"""
        # 攻击条积攒：**不截断**到 100，多出的点数保留下来。
        # 旧写法 min(100, gauge + fill) 会把高速度多攒的点直接丢弃——
        # 速度 25 每帧攒 125，被压成 100，行动一次后归零，白丢 25 点，
        # 于是 20 以上全部卡在每回合 10 次（速度投资完全无效）。
        self.gauge_pet += self._spd_gauge_gain(self._pet_spd_eff())
        for rec in self._live_units():
            speed = self._spend_stun(rec)
            rec["gauge"] += self._spd_gauge_gain(speed)

        # 决定行动顺序并执行；同一帧内攒够 200 点就能连动两次。
        # 每次行动即扣 100，剩余点数带入下一次，行动条不再有上限封顶。
        # 注意：每个 break 都必须跳出**整个 while**（用标志位 + 统一的
        # ``battle_over`` 判断），否则战斗结束后 while 还会继续空转——
        # 因为此时没人能消耗掉剩下的行动条，就是一个死循环。
        guard = 0
        max_actions = self.max_actions_per_frame
        while guard < max_actions:
            if self.pet_hp <= 0 or self._pick_target() is None:
                break
            actors: list[tuple[str, float, dict[str, Any] | None]] = []
            if self.gauge_pet >= 100.0:
                actors.append(("pet", float(self._pet_spd_eff()), None))
            for rec in self._live_units():
                if rec["gauge"] >= 100.0:
                    actors.append(("monster", float(rec["unit"]["spd"]), rec))
            if not actors:
                break
            actors.sort(key=lambda a: -a[1])

            for who, _, rec in actors:
                guard += 1
                if guard > max_actions:
                    break
                if self.pet_hp <= 0 or self._pick_target() is None:
                    break
                if rec is not None and rec["unit"]["hp"] <= 0:
                    continue
                if who == "pet":
                    self._pet_action()
                else:
                    assert rec is not None
                    # 本轮被「不可行动」打断并取消行动的：扣掉行动条直接跳过，
                    # 不写日志（控制本身已经由「被打断了」那条说明）。
                    if rec.pop("skip_round", False):
                        rec["gauge"] -= 100.0
                        continue
                    rec["gauge"] -= 100.0
                    self.monster_acts += 1
                    self._enemy_action(rec)

        # 轮末处理
        self._end_of_round()

    def _spend_stun(self, rec: dict[str, Any]) -> float:
        """用速度抵扣「不可行动」值，返回本轮可用于攒条的速度。

        抵扣顺序：先扣眩晕值，剩下的速度才进行动条——所以眩晕期间不仅不攒条，
        连当轮本会攒到的那份也一并被吃掉。抵扣完当轮立即解除。
        """
        speed = float(int(rec["unit"]["spd"]))
        remaining = float(rec.get("stun", 0.0))
        if remaining <= 0:
            return speed
        if speed <= 0:
            return 0.0
        used = min(speed, remaining)
        left = remaining - used
        rec["stun"] = left
        # 抵扣过程不写日志：每帧一条会把 10 条战报名额吃掉，
        # 玩家只需要知道「蓄力被打断了」，不需要看它逐点抵扣。
        return max(0.0, speed - used)

    def _end_of_round(self) -> None:
        """帧末处理：冷却、计时条、削弱与持续伤害。

        所有**有时长的效果**（技能冷却、削弱、DoT）都记「剩余帧数」并在每帧 -1，
        而不是挂在「回合末」统一结算。原因：挂在回合末会让实际时长取决于
        释放时刻——回合快结束时放技能，下一帧就跨过边界白送一次 -1，
        等于可以极短时间连放；削弱同理，刚施加就掉一层。

        计时条（每帧 +``timer_gain``、满 100）现在只负责两件事：
        「回合」的对外计数，以及回蓝（我方 ``per_turn_pct`` + 敌方 ``regen``）。
        """
        # —— 冷却：每个技能**各自**从释放那一刻起累积帧数 ——
        # 旧写法在「回合末」统一 -1，导致「回合快结束时释放 → 下一帧计时条满
        # → 立刻 -1」，可以在极短时间内连放。现在冷却记的是**剩余帧数**，
        # 每帧 -1，与计时条、与其它技能的释放时机都无关。
        if self.cooldowns:
            for skill_id in self.cooldowns:
                if self.cooldowns[skill_id] > 0:
                    self.cooldowns[skill_id] -= 1
        for rec in self._live_units():
            for skill_id in rec["cd"]:
                if rec["cd"][skill_id] > 0:
                    rec["cd"][skill_id] -= 1

        # —— 计时条：满 100 = 一个回合结束（回蓝 + 敌方回血）——
        self.battle_timer += self.timer_gain
        if self.battle_timer >= 100.0:
            self.battle_timer -= 100.0
            # 回合结束：我方回蓝（每回合一次，开局那回合不计）。
            self._regen_turn_mp()
            for rec in self._live_units():
                rec["mp"] = min(rec["mp_cap"], rec["mp"] + self.mp_regen)
            # 敌方再生（monster_traits 的 regen_pct）：按回合结算，
            # 因为它本来就是「随时间回复」而非「按出手次数回复」。
            self._unit_regen()
            # 精英专属机制「抽蓝」：同样按回合结算，紧跟在双方回蓝之后，
            # 所以同一个回合里玩家看到的是「先回一点、又被抽走一截」。
            self._passive_siphon()

        # —— 削弱消退：每个 debuff **各自**从施加时起累积帧 ——
        # 与冷却同样的理由：旧写法挂在「回合末」，施加减益后一跨过回合边界
        # 就立刻掉一层，实际持续时间取决于释放时机而非配置值。
        if self.pet_debuffs:
            expired = []
            for entry in self.pet_debuffs:
                entry["frames"] = int(entry.get("frames", 0)) - 1
                if entry["frames"] <= 0:
                    expired.append(entry)
            if expired:
                for entry in expired:
                    self.pet_debuffs.remove(entry)
                self._refresh_pet_mods()
                self._blog(
                    "log_debuff_end",
                    "回合{round} 你身上的削弱效果消退。",
                )

        # —— 持续伤害：同样各自累积帧，按「每满一个回合」结算一次 ——
        for entry in list(self.dots):
            entry["frames"] = int(entry.get("frames", 0)) - 1
            if entry["frames"] <= 0:
                tick = max(0, round(int(entry["atk"]) * float(entry["frac"])))
                self.pet_hp = max(1, self.pet_hp - tick)
                if tick > 0:
                    self._blog(
                        "log_dot_tick",
                        "回合{round} 持续伤害造成 {dmg} 点（你剩余 {hp}）。",
                        dmg=tick,
                        hp=self.pet_hp,
                    )
                entry["ticks"] = int(entry.get("ticks", 0)) - 1
                if entry["ticks"] <= 0:
                    self.dots.remove(entry)
                    self._blog("log_dot_end", "回合{round} 持续伤害结束。")
                else:
                    entry["frames"] = self.frames_per_turn

        # 形态判定
        for rec in self._live_units():
            self._unit_phases(rec)


def _auto_battle(
    pet,
    run: dict[str, Any],
    monster: dict[str, Any],
    cfg: dict[str, Any],
    rng: random.Random,
    mults: dict[str, float] | None = None,
    log: list[tuple[int, str]] | None = None,
) -> BattleResult:
    """ATB 自动战斗。返回战斗结果。

    这是 BattleSession.run() 的便捷包装器。
    """
    session = BattleSession(pet, run, monster, cfg, rng, mults, log)
    return session.run()


async def _do_battle(
    pet,
    rogue,
    cfg: dict[str, Any],
    *,
    enemy_index: int | None = None,
    elite: bool | None = None,
) -> str:
    """战斗入口：生成怪物、跑自动战斗、渲染战报、处理掉落与推进。

    ``enemy_index`` 是普通层选项里预抽好的敌人池下标（精英层则是 ``region.elites``
    池的下标，见 ``FloorChoiceState``）；``elite`` 缺省时沿用 pending 里的层内精英标记。
    """
    from ._common import _persist, _text, floor_rng
    from .relics import (
        _grant_relic,
        _run_relics,
        relic_effect_note,
        relic_flat,
        relic_multipliers,
    )
    from .settle import settle_run
    from .tower import (
        _gen_monster,
        boss_floor_set,
        region_of,
    )

    run = rogue.run_state
    floor = run["floor"]
    region = region_of(floor, cfg)
    rng = floor_rng(run["seed"], floor, "battle")
    pending = run.get("pending") or {}
    is_elite = bool(pending.get("elite")) if elite is None else elite
    boss_index = pending.get("boss_index")
    # 事件触发的强化精英：pending 里带 elite_fight 参数（见 floor._resolve_event）。
    elite_fight = pending.get("elite_fight")
    if elite_fight:
        is_elite = True
    monster = _gen_monster(
        floor,
        region,
        cfg,
        rng,
        boss_index=boss_index,
        elite=is_elite,
        pool_index=None if is_elite else enemy_index,
        elite_index=enemy_index if is_elite else None,
        elite_fight=elite_fight,
    )

    mults = relic_multipliers(rogue)
    # 群怪是多个独立单位：monster["hp"] 只是**单只**血量，战报要用全员合计。
    pack_count = max(1, int((monster.get("pack") or {}).get("count", 1) or 1))
    monster_hp_full = int(monster["hp"]) * pack_count
    log_limit = int((cfg.get("battle") or {}).get("log_limit", 0))
    battle_log: list[tuple[int, str]] | None = [] if log_limit > 0 else None
    result = _auto_battle(pet, run, monster, cfg, rng, mults, battle_log)
    run["hp"] = max(0, result.pet_hp)
    # 魔力是局内跨层资源：战斗结束把当前值写回 run，下一层接着用。
    run["mp"] = max(0, int(result.pet_mp))
    run["mp_max"] = int(result.mp_cap)
    if monster.get("boss"):
        header_key, header_default = (
            "battle_header",
            "⚔️ 第 {floor} 层 · {region}：{name} VS {monster}",
        )
    elif monster.get("elite_fight"):
        header_key, header_default = (
            "elite_fight_header",
            "🔥 强化精英战 · {title} · 第 {floor} 层 · {region}：{name} VS {monster}",
        )
    elif is_elite:
        header_key, header_default = (
            "elite_header",
            "⚠️ 精英战 · 第 {floor} 层 · {region}：{name} VS {monster}",
        )
    else:
        header_key, header_default = (
            "battle_header",
            "⚔️ 第 {floor} 层 · {region}：{name} VS {monster}",
        )
    lines = [
        _text(
            header_key,
            header_default,
            floor=floor,
            region=region.get("name", ""),
            name=pet.name,
            title=str(monster.get("elite_fight_title") or "强化形态"),
            monster=_monster_name(monster),
        )
    ]
    # 战斗详情：怪物描述（小怪/精英/首领都按 desc 渲染），特点单独一行。
    desc_line = _monster_desc(monster)
    if desc_line:
        lines.append(desc_line)
    trait_line = _monster_trait_line(monster)
    if trait_line:
        lines.append(trait_line)
    # 群怪：明确告诉玩家「这是 N 只，各自一条命」，别让人以为是共用一个血条。
    if pack_count > 1:
        lines.append(
            _text(
                "battle_pack_line",
                "🐝 一群 {count} 只（各自生命 {hp_each}，需逐只击倒）",
                count=pack_count,
                hp_each=int(monster["hp"]),
            )
        )
    if monster.get("boss") or is_elite:
        lines.extend(_enemy_skill_lines(monster, cfg, hint=True))
    lines.append(
        _text(
            "battle_pace",
            "⏱ 第 {rounds} 轮｜出击：你 {pa} 次 / 敌 {ma} 次（暴击 {crits} 次）",
            rounds=result.rounds,
            pa=result.pet_acts,
            ma=result.monster_acts,
            crits=result.crits,
        )
    )
    # 逐条战报（battle.log_limit 条，默认 10）：取最后 N 条。
    # log_limit = 0 时整个段落不渲染，只留标题、描述、节奏与结果。
    if battle_log is not None:
        shown = battle_log[-log_limit:]
        lines.append(
            _text(
                "battle_log_header",
                "📖 战报（最近 {count} 条）：",
                count=len(shown),
            )
        )
        if shown:
            lines.extend(line for _, line in shown)
        else:
            lines.append(_text("log_none", "（战斗过于漫长，战报已截断）"))

    if not result.victory:
        if run["hp"] <= 0:
            lines.append(
                _text(
                    "battle_loss",
                    "💀 被 {monster} 击败……（{rounds} 回合）我方生命归零，敌方剩余 {monster_hp}/{monster_hp_full}，本局结束，按到达层数结算。",
                    monster=_monster_name(monster),
                    rounds=result.rounds,
                    monster_hp=result.enemy_hp,
                    monster_hp_full=monster_hp_full,
                )
            )
        else:
            lines.append(
                _text(
                    "battle_draw",
                    "⏱ 与 {monster} 鏖战 {rounds} 回合未分胜负……我方剩余 {hp}/{hp_max}，敌方剩余 {monster_hp}/{monster_hp_full}，本局结束，按到达层数结算。",
                    monster=_monster_name(monster),
                    rounds=result.rounds,
                    hp=run["hp"],
                    hp_max=run["hp_max"],
                    monster_hp=result.enemy_hp,
                    monster_hp_full=monster_hp_full,
                )
            )
        await _persist(rogue)
        lines.append(await settle_run(pet, rogue, reason="defeat"))
        return "\n".join(lines)

    coin_gain = round(monster["coins"] * mults.get("coins_mult", 1.0))
    exp_gain = round(monster["exp"] * mults.get("exp_mult", 1.0))
    run["coins"] += coin_gain
    run["exp"] += exp_gain
    lines.append(
        _text(
            "battle_win",
            "✅ 战胜 {monster}！（{rounds} 回合）生命 {hp}/{hp_max}，局内金币 +{coins}（现有 {total}），本局经验 +{exp}",
            monster=_monster_name(monster),
            rounds=result.rounds,
            hp=run["hp"],
            hp_max=run["hp_max"],
            coins=coin_gain,
            total=run["coins"],
            exp=exp_gain,
        )
    )

    # 强化精英胜利：掉落一件遗物（不做金币/经验翻倍）。
    if monster.get("elite_fight"):
        from .relics import elite_fight_relic

        dropped = elite_fight_relic(
            rogue,
            floor,
            int(run.get("seed", 0)),
            str(monster.get("elite_fight_relic") or "auto"),
        )
        if dropped:
            # 同 Boss 遗物：_grant_relic 改写的是 rogue.run_state，
            # 必须先落 run 再发，否则会被随后的 to_dict 覆盖。
            spec = _grant_relic(rogue, dropped) or {}
            await _persist(rogue)
            lines.append(
                _text(
                    "elite_fight_relic_gained",
                    "🔥 强化精英掉落（本局生效）：{name}（{stat}） — {desc}",
                    name=spec.get("name", dropped),
                    stat=relic_effect_note(spec.get("effect") or {}),
                    desc=spec.get("desc", ""),
                )
            )
        else:
            lines.append(
                _text(
                    "elite_fight_relic_none",
                    "🔥 强化精英已无新的遗物可掉（本局普通遗物已集齐）。",
                )
            )

    # 首通记录：精英 / 首领都按「区域」记一次（键见 roguelike.yaml 的
    # stage_skill_rewards）。只在尚未解锁时记入 run，避免每次战斗都刷提示。
    stage_rewards = (cfg.get("stage_skill_rewards") or {}).get(region.get("id", ""))
    stage_rewards = stage_rewards if isinstance(stage_rewards, dict) else {}
    unlock_key = ""
    if monster.get("boss") and stage_rewards.get("boss"):
        unlock_key = f"boss:{region.get('id', '')}"
    elif is_elite and stage_rewards.get("elite"):
        unlock_key = f"elite:{region.get('id', '')}"
    # 同一次 run 里可能打赢多只精英，而 rogue.unlocked 要到结算才写回；
    # 这里必须连 run 自己的待结算列表一起判，避免重复提示与重复记账。
    run_unlocks = run.setdefault("unlocks", [])
    if (
        unlock_key
        and unlock_key not in rogue.unlocked
        and unlock_key not in run_unlocks
    ):
        run_unlocks.append(unlock_key)
        lines.append(
            _text(
                "stage_unlock_hint",
                "🔓 首次击败本区域{kind}，阶段奖励技能将于结算时发放。",
                kind="首领" if monster.get("boss") else "精英",
            )
        )

    if monster.get("boss"):
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
        if unlock is not None and str(floor) not in rogue.unlocked:
            run.setdefault("unlocks", []).append(str(floor))
            lines.append(
                _text("boss_unlock_hint", "🔓 首通奖励将于结算时发放。", floor=floor)
            )
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
                        "👑 Boss 专属遗物入手（本局生效）：{name}（{stat}） — {desc}",
                        name=spec.get("name", relic_id),
                        stat=relic_effect_note(spec.get("effect") or {}),
                        desc=spec.get("desc", ""),
                    )
                )
        if floor == int(cfg["tower"]["floors"]):
            run["cleared"] = True
            await _persist(rogue)
            lines.append(await settle_run(pet, rogue, reason="clear"))
            return "\n".join(lines)

    from .run_state import advance as advance_floor

    step_text = await advance_floor(pet, rogue)
    return "\n".join(lines) + "\n" + step_text
