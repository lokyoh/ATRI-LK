"""战斗引擎：ATB 攻击条模型、自动战斗与战报。"""

import random
from dataclasses import dataclass
from typing import Any

from ..skills import (
    equipped_specs,
    get_skill_power,
    increase_proficiency,
)
from ._common import (
    BASE_SKILL_POWER,
    _load_skills_config,
    _text,
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


def _damage(
    atk: int, defence: int, variance: list[float], def_factor: float, rng: random.Random
) -> int:
    """基础伤害公式。"""
    raw = (atk - defence * def_factor) * rng.uniform(*variance)
    return max(1, round(raw))


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
        self.def_factor = float(battle_cfg.get("def_factor", 0.5))
        self.max_rounds = int(battle_cfg.get("max_rounds", 500))
        self.gauge_rate_mult = float(battle_cfg.get("gauge_rate_mult", 5))
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
        self._setup_enemy_mp()

        # 战斗单位
        self.units: list[dict[str, Any]] = [
            self._spawn_unit(monster, primary=True, minion=False)
        ]
        self.dots: list[dict[str, Any]] = []
        self.pet_debuffs: list[dict[str, Any]] = []
        self.pet_mods = {"atk_mult": 1.0, "def_mult": 1.0, "spd_mult": 1.0}
        self.current_target: dict[str, Any] | None = None

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

    def _pet_damage(self, defence: int, bossy: bool) -> tuple[int, bool]:
        """我方输出（遗物 vs_boss_mult × 被动 dmg_mult × 被动 crit_rate 暴击）。"""
        mult = (self.out_mult if bossy else 1.0) * self.dmg_mult
        dmg = max(
            1,
            round(
                _damage(
                    self._pet_atk_eff(),
                    defence,
                    self.variance,
                    self.def_factor,
                    self.rng,
                )
                * mult
            ),
        )
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

        if self.current_target is not None and target is not self.current_target:
            self._blog(
                "log_target",
                "回合{round} 你的攻击转向 {name}。",
                name=str(target["unit"]["name"]),
            )
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
                    self.cooldowns[skill_id] = int(skill.get("cooldown", 0))
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
                key=lambda pair: -get_skill_power(self.pet, pair[0], self.skills_cfg),
            )
            for skill_id, skill in ranked:
                cost = int(skill.get("mp_cost", 0))
                if self.pet_mp < cost:
                    continue
                self.pet_mp -= cost
                factor = (
                    get_skill_power(self.pet, skill_id, self.skills_cfg)
                    / BASE_SKILL_POWER
                )
                strikes = max(1, int(skill.get("strikes", 1) or 1))
                dealt_total = 0
                crit_any = False
                for _ in range(strikes):
                    if target["unit"]["hp"] <= 0:
                        break
                    dmg, is_crit = self._pet_damage(
                        int(target["unit"]["def"]), bool(target["primary"])
                    )
                    dealt = max(1, round(dmg * factor))
                    target["unit"]["hp"] -= dealt
                    dealt_total += dealt
                    crit_any = crit_any or is_crit
                if strikes > 1:
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
                self._charge_taken(target, dealt_total)
                self._strike_back(target["unit"], dealt_total)
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
                self.cooldowns[skill_id] = int(skill.get("cooldown", 0))
                increase_proficiency(self.pet, skill_id, skills_config=self.skills_cfg)
                acted = True
                break

        # 普攻
        if not acted:
            dmg, is_crit = self._pet_damage(
                int(target["unit"]["def"]), bool(target["primary"])
            )
            target["unit"]["hp"] -= dmg
            self._blog(
                "log_attack",
                "回合{round} {actor} 普攻，造成 {dmg} 伤害。",
                actor=self.pet.name,
                dmg=dmg,
            )
            self._blog_crit(is_crit)
            self._charge_taken(target, dmg)
            self._strike_back(target["unit"], dmg)

        self._settle_deaths()

    # -------------------------------------------------------------------------
    # 敌方侧
    # -------------------------------------------------------------------------

    def _skill_label(self, skill: dict[str, Any], skill_id: str) -> str:
        return str(skill.get("name") or skill_id)

    def _unit_skills(self, unit: dict[str, Any]) -> list[str]:
        return [
            str(sid)
            for sid in (unit.get("skills") or [])
            if str(sid) in self.enemy_skills_cfg
        ]

    def _spawn_unit(
        self, unit: dict[str, Any], *, primary: bool, minion: bool
    ) -> dict[str, Any]:
        """生成战斗单位。"""
        skills = self._unit_skills(unit)
        mult = self.minion_mp_mult if minion else self.primary_mp_mult
        pool = max(0, int(self.mp_base * mult))
        return {
            "unit": unit,
            "primary": primary,
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
        """击倒结算。"""
        primary = self.units[0]
        if primary["unit"]["hp"] <= 0:
            for record in self.units:
                if record is not primary:
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
        rec["cd"][skill_id] = int(skill.get("cooldown", 0))
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
        self._blog(
            "log_unactionable",
            "回合{round} {actor} 陷入「不可行动」（{value} 点，需以速度抵扣）。",
            actor=rec["unit"]["name"],
            value=round(gain),
        )

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
                dmg = max(
                    1,
                    round(
                        _damage(
                            int(unit["atk"]),
                            self._pet_def_eff(),
                            self.variance,
                            self.def_factor,
                            self.rng,
                        )
                        * (power / BASE_SKILL_POWER)
                    ),
                )
                if rate > 0 and self.rng.random() < rate:
                    dmg = max(1, round(dmg * self.crit_mult))
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
            entry: dict[str, Any] = {
                "id": skill_id,
                "rounds": max(1, int(debuff.get("rounds", 1))),
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
            self.dots.append(
                {
                    "atk": int(unit["atk"]),
                    "frac": float(dot.get("frac", 0.0)),
                    "rounds": max(1, int(dot.get("rounds", 1))),
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
            dmg = _damage(
                int(unit["atk"]),
                self._pet_def_eff(),
                self.variance,
                self.def_factor,
                self.rng,
            )
            if rate > 0 and self.rng.random() < rate:
                dmg = max(1, round(dmg * self.crit_mult))
            dmg = max(1, round(dmg * strike_mult))
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
        rec["cd"][skill_id] = int(skill.get("cooldown", 0))
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
        )

    def _run_round(self) -> None:
        """执行一轮。"""
        # 攻击条积攒
        self.gauge_pet = min(
            100.0, self.gauge_pet + self._pet_spd_eff() * self.gauge_rate_mult
        )
        for rec in self._live_units():
            speed = self._spend_stun(rec)
            rec["gauge"] = min(100.0, rec["gauge"] + speed * self.gauge_rate_mult)

        # 决定行动顺序
        actors: list[tuple[str, int, dict[str, Any] | None]] = []
        if self.gauge_pet >= 100.0:
            actors.append(("pet", self._pet_spd_eff(), None))
        for rec in self._live_units():
            if rec["gauge"] >= 100.0:
                actors.append(("monster", int(rec["unit"]["spd"]), rec))
        actors.sort(key=lambda a: -a[1])

        # 执行行动
        for who, _, rec in actors:
            if self.pet_hp <= 0 or self._pick_target() is None:
                break
            if rec is not None and rec["unit"]["hp"] <= 0:
                continue
            if who == "pet":
                self._pet_action()
                if self.pet_hp <= 0:
                    break
            else:
                assert rec is not None
                # 本轮被「不可行动」打断并取消行动的，直接跳过（见 _apply_unactionable）。
                if rec.pop("skip_round", False):
                    self._blog(
                        "log_unactionable_skip",
                        "回合{round} {actor} 因「不可行动」未能出手。",
                        actor=rec["unit"]["name"],
                    )
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
        self._blog(
            "log_unactionable_tick",
            "回合{round} {actor} 处于「不可行动」，速度被抵消 {used} 点（剩余 {left}）。",
            actor=rec["unit"]["name"],
            used=round(used),
            left=round(left),
        )
        if left <= 0:
            self._blog(
                "log_unactionable_end",
                "回合{round} {actor} 挣脱了「不可行动」。",
                actor=rec["unit"]["name"],
            )
        return max(0.0, speed - used)

    def _end_of_round(self) -> None:
        """帧末处理：计时条、冷却、持续伤害。

        计时条每帧 +timer_gain，走满 100 即「一个回合结束」——回合回蓝、
        冷却 -1、削弱/DoT 结算都挂在这里，而不是挂在某次行动上。
        """
        self.battle_timer += self.timer_gain
        if self.battle_timer >= 100.0:
            self.battle_timer -= 100.0
            # 回合结束：我方回蓝（每回合一次，开局那回合不计）。
            self._regen_turn_mp()
            # 冷却 -1
            for skill_id in self.cooldowns:
                if self.cooldowns[skill_id] > 0:
                    self.cooldowns[skill_id] -= 1
            for rec in self._live_units():
                for skill_id in rec["cd"]:
                    if rec["cd"][skill_id] > 0:
                        rec["cd"][skill_id] -= 1
                rec["mp"] = min(rec["mp_cap"], rec["mp"] + self.mp_regen)
            # 削弱消退
            expired = []
            for entry in self.pet_debuffs:
                entry["rounds"] -= 1
                if entry["rounds"] <= 0:
                    expired.append(entry)
            if expired:
                for entry in expired:
                    self.pet_debuffs.remove(entry)
                self._refresh_pet_mods()
                self._blog(
                    "log_debuff_end",
                    "回合{round} 你身上的削弱效果消退。",
                )
            # 持续伤害
            for entry in list(self.dots):
                tick = max(0, round(int(entry["atk"]) * float(entry["frac"])))
                self.pet_hp = max(1, self.pet_hp - tick)
                if tick > 0:
                    self._blog(
                        "log_dot_tick",
                        "回合{round} 持续伤害造成 {dmg} 点（你剩余 {hp}）。",
                        dmg=tick,
                        hp=self.pet_hp,
                    )
                entry["rounds"] -= 1
                if entry["rounds"] <= 0:
                    self.dots.remove(entry)
                    self._blog("log_dot_end", "回合{round} 持续伤害结束。")
            self._blog(
                "log_timer",
                "回合{round} ⏱ 计时条满：双方技能冷却 -1。",
            )
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
    monster = _gen_monster(
        floor,
        region,
        cfg,
        rng,
        boss_index=boss_index,
        elite=is_elite,
        pool_index=None if is_elite else enemy_index,
        elite_index=enemy_index if is_elite else None,
    )

    mults = relic_multipliers(rogue)
    monster_hp_full = int(monster["hp"])
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
                    monster_hp=max(0, int(monster["hp"])),
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
                    monster_hp=max(0, int(monster["hp"])),
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
