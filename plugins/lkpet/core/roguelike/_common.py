"""肉鸽系统共享常量、配置加载器与工具函数。"""

import random
from typing import Any

"""行动点键（见 daily.yaml action_points.costs 与 roguelike.yaml）。"""
ENTRY_COST_KEY = "rogue"
STEP_COST_KEY = "rogue_step"

BASE_SKILL_POWER = 30.0
"""技能威力基准（= skills.yaml 撞击 tackle 的 power）。

伤害技能的伤害系数 = ``get_skill_power(含熟练度加成) / BASE_SKILL_POWER``：
威力 30 → 1.0×（等于普攻但白赚熟练度），火球术 40 → 1.33×，肉鸽斩击 55 → 1.83×。
"""

PASSIVE_STAT_KEYS = (
    "atk",
    "def",
    "spd",
    "mp",
    "max_hp",
    "crit_rate",
    "dmg_mult",
)
"""被动技能可用的加成键（见 skills.yaml 的被动说明）。"""


def _load_rogue_config() -> dict[str, Any]:
    from ...config_loader import load_config

    return load_config("roguelike")


def _load_skills_config() -> dict[str, Any]:
    from ...config_loader import load_config

    return load_config("skills")


def _load_events_config() -> dict[str, Any]:
    from ...config_loader import load_config

    return load_config("rogue_events")


def _load_relics_config() -> dict[str, Any]:
    from ...config_loader import load_config

    return load_config("rogue_relics")


def _text(key: str, default: str = "", **fields: Any) -> str:
    from ...utils.texts import text

    return text(f"rogue.{key}", default, **fields)


async def _persist(rogue) -> None:
    from ...database import save_rogue

    save_rogue(rogue)


def floor_rng(seed: int, floor: int, purpose: str) -> random.Random:
    """每层每用途独立的随机源：确定性、可复现、互不干扰。"""
    return random.Random(f"{seed}:{floor}:{purpose}")


def passive_effects(pet, skills_cfg: dict[str, Any] | None = None) -> dict[str, float]:
    """汇总**装备中**被动技能（type: passive）的 effect，返回战斗/局内加成。

    只有 ``pet.skill_equipped`` 里的被动生效——未装备的留在技能库里不提供加成。
    键见 skills.yaml 的被动注释：atk/def/spd/mp 加在入场战斗属性上、
    max_hp 加在局内生命上限（new_run）上、crit_rate 为我方输出暴击率、
    dmg_mult 为我方输出乘区。
    """
    from ..skills import passive_bonuses

    return passive_bonuses(pet, skills_cfg)


# ---------------------------------------------------------------------------
# 局内魔力（MP）
# ---------------------------------------------------------------------------


def rogue_mp_max(
    pet,
    relic_ids: list[str] | None = None,
    cfg: dict[str, Any] | None = None,
    skills_cfg: dict[str, Any] | None = None,
) -> int:
    """局内魔力上限：养成 ``mp``（×遗物乘区） + 被动 mp + 遗物平值 mp。

    战斗入场与局内推进共用这一个口径，避免「战斗里 60 蓝、进度里 50 蓝」
    两处算不一样。``mp`` 在肉鸽里是**跨层保留**的资源（见 roguelike.yaml
    的 ``battle.mp``），不是每场战斗重置。
    """
    from .relics import relic_multipliers_from_ids, relic_stat_bonuses

    passives = passive_effects(pet, skills_cfg)
    relics = relic_stat_bonuses(list(relic_ids or []))
    mult = float(
        (relic_multipliers_from_ids(list(relic_ids or [])) or {}).get("mp_mult", 1.0)
    )
    base = int(int((getattr(pet, "attributes", {}) or {}).get("mp", 20)) * mult)
    return max(1, base + int(passives["mp"]) + int(relics["mp"]))


def battle_mp_cfg(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """``battle.mp`` 块：每层 / 每回合的魔力回复比例。"""
    return ((cfg or {}).get("battle") or {}).get("mp") or {}


def mp_regen_pct(
    cfg: dict[str, Any] | None = None, key: str = "per_floor_pct"
) -> float:
    """读取 ``battle.mp`` 里的某个回复比例。

    * ``per_floor_pct``：每前进一层回复上限的百分比（默认 20%）
    * ``per_turn_pct``：**每个战斗回合**（= 我方每次行动）回复上限的百分比（默认 5%）
    * ``rest_floor_pct``：休息层（区域倒数第二层）额外回复（默认 40%）
    """
    defaults = {"per_floor_pct": 0.2, "per_turn_pct": 0.05, "rest_floor_pct": 0.4}
    try:
        return float(battle_mp_cfg(cfg).get(key, defaults.get(key, 0.0)) or 0.0)
    except (TypeError, ValueError):
        return defaults.get(key, 0.0)


# ---------------------------------------------------------------------------
# 不可行动（打断蓄力后的硬控）
#
# 注意：本插件只有一种硬控状态，统一叫「不可行动」。旧版临时命名「眩晕」
# 已废弃——不要再用眩晕命名任何字段、配置键或文案。
# ---------------------------------------------------------------------------


def unactionable_cfg(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """``battle.unactionable`` 块：被打断蓄力后施加的「不可行动」参数。

    数值单位是**速度点**（= 目标速度 × 回合数），因此对高速度的首领同样有效：
    高速度的怪每轮攒条更快，被扣除的速度点也更多，二者按比例抵消。
    """
    defaults = {
        "enabled": True,
        "default_rounds": 1.0,
        "max_rounds": 2.5,
        "stack_limit_rounds": 4.0,
        "boss_resist": 0.8,
        "charge_bonus": 0.5,
        "reset_gauge": True,
    }
    node = ((cfg or {}).get("battle") or {}).get("unactionable")
    merged = dict(defaults)
    if isinstance(node, dict):
        merged.update(node)
    return merged


async def get_or_create_rogue(player_id: str | int):
    """取回肉鸽进度行；不存在时按默认值建行。"""
    from ...database import Rogue, load_rogue, save_rogue

    rogue = load_rogue(player_id)
    if rogue is None:
        rogue = Rogue(player_id=str(player_id))
        save_rogue(rogue)
    return rogue


def _entry_cost() -> int:
    from ..interaction import _action_costs

    return int(_action_costs(None).get(ENTRY_COST_KEY, 0))


def _find_event(
    event_id: Any, region: dict[str, Any], cfg: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """在该区域的事件表（rogue_events.yaml 按区域分组）里按 id 查找事件。

    ``cfg`` 传入时额外支持 supply 回血补给事件（首领前一层的固定事件，
    定义在 roguelike.yaml 的 ``supply.event``）。
    """
    if cfg is not None:
        supply_event = (cfg.get("supply") or {}).get("event")
        if supply_event and supply_event.get("id") == event_id:
            return supply_event
    from .tower import _region_events

    for event in _region_events(region):
        if event.get("id") == event_id:
            return event
    return None
