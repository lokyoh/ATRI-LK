"""肉鸽系统共享常量、配置加载器与工具函数。"""

import random
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

#: 渲染时是否在正文里列「1. 战斗 / 2. 事件」这类编号选项。
#:
#: QQ 官方适配器会把选项做成底部按钮，正文再列一遍就是同一件事说两次；
#: OneBot 端没有按钮，编号必须留着——否则玩家不知道该回什么数字。
#: 用 ContextVar 而不是给每个 ``render`` 加参数：那要改七个子类的方法签名，
#: 而这是「这次渲染怎么排版」的全局开关，天然适合上下文。
_SHOW_NUMBERED_OPTIONS: ContextVar[bool] = ContextVar(
    "lkpet_show_numbered_options", default=True
)


def show_numbered_options() -> bool:
    """当前这次渲染要不要列编号选项。"""
    return _SHOW_NUMBERED_OPTIONS.get()


#: 把「一行内容 + 指令」变成可点链接的构造器。
#:
#: core 层不该知道 QQ 的 ``<qqbot-cmd-input>`` 长什么样，所以由上层注入：
#: handler 渲染前放一个 ``(text, command) -> str`` 进来，core 只管调用。
#: 没有注入时按纯文本返回（OneBot / 单测环境）。
_LINE_LINK: ContextVar[Any] = ContextVar("lkpet_line_link", default=None)


def line_link(text: str, command: str) -> str:
    """把整行文本变成可点链接。

    这正是 lkbot 商品列表的做法——整个商品名就是按钮，而不是「名字 + 行尾的
    『购买』」。用户看到什么就点什么，不用把行号换算成底部的第几个链接。

    :param text: 这一行要显示的内容（整行都会变成可点区域）
    :param command: 点下去发出的完整指令
    """
    builder = _LINE_LINK.get()
    if builder is None:
        return text
    try:
        return builder(text, command)
    except Exception:
        return text


@contextmanager
def line_links(builder):
    """在 ``with`` 范围内启用行内链接构造。"""
    token = _LINE_LINK.set(builder)
    try:
        yield
    finally:
        _LINE_LINK.reset(token)


@contextmanager
def numbered_options(enabled: bool):
    """在 ``with`` 范围内控制编号选项的显示。"""
    token = _SHOW_NUMBERED_OPTIONS.set(bool(enabled))
    try:
        yield
    finally:
        _SHOW_NUMBERED_OPTIONS.reset(token)


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


def _style():
    """本次渲染的排版能力（QQ 端 markdown / OneBot 端纯文本）。

    core 层只表达「这是标题、这是分节」，具体长什么样由 ``utils.style`` 决定；
    这样肉鸽的十几张卡不必各写一份 ``if is_qq()``。
    """
    from ...utils import style

    return style.current()


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
    * ``per_turn_pct``：**每个战斗回合结束时**回复上限的百分比（默认 5%）。
      回合 = 计时条走满一次 = ``frames_per_turn`` 帧，**不是**每次行动；
      一回合内双方可以各行动 8~10 次，挂在行动上会变成每出手都回蓝。
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


# ---------------------------------------------------------------------------
# 等级战力（等级进入战斗公式）
# ---------------------------------------------------------------------------


def level_power_cfg(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """``battle.level_power`` 块：等级对战斗力的乘区参数。

    设计意图：让「练级」成为真正的战力来源，而不是只靠遗物与技能。敌方没有
    等级概念（难度只由层数决定），因此把**层号换算成期望等级**当基准：

    ``期望等级 = floor × ref_level_per_floor``

    低于期望等级 → 输出/减伤乘区 < 1（打不动、也不耐打）；高于 → > 1。

    见 :func:`level_combat_mults`。
    """
    defaults = {
        "enabled": True,
        "ref_level_per_floor": 1.0,
        "atk_per_level": 0.01,
        "def_per_level": 0.012,
        "min_mult": 0.5,
        "max_mult": 1.9,
        "apply_to": "both",
    }
    node = ((cfg or {}).get("battle") or {}).get("level_power")
    merged = dict(defaults)
    if isinstance(node, dict):
        merged.update(node)
    return merged


def level_combat_mults(
    pet_level: int,
    enemy_level: int,
    cfg: dict[str, Any] | None = None,
) -> tuple[float, float]:
    """按「宠物等级 vs 怪物等级」算出 ``(我方输出乘区, 我方承伤乘区)``。

    这是等级进入战斗公式的地方——**双方等级直接对比**，不再用层号换算：

    * **输出乘区**乘在我方造成的伤害上（普攻与技能都走 ``_pet_damage``）；
    * **承伤乘区**乘在敌方打到我方之后的伤害上——``< 1`` 表示更耐打。

    两个值都由 ``1 + 每级百分点 × (宠物等级 - 怪物等级)`` 得到，再钳制到
    ``[min_mult, max_mult]``；承伤额外取倒数，所以「等级高 → 更耐打」。

    例（默认 atk 0.01 / def 0.012、怪物等级 = 层号 = 80）：

    ====== ====== ========== ==========
    宠物   怪物   输出乘区   承伤乘区
    ====== ====== ========== ==========
    18     80     0.50(下限) 1.72
    50     80     0.70       1.44
    80     80     1.00       1.00
    100    80     1.20       0.86
    ====== ====== ========== ==========

    ``apply_to`` 控制只加哪一侧（``atk`` / ``def`` / ``both``）；
    ``enabled: false`` 时恒为 ``(1.0, 1.0)``，即退回改动前的行为。
    """
    node = level_power_cfg(cfg)
    if not node.get("enabled", True):
        return 1.0, 1.0

    # 怪物等级即层号（见 tower._gen_monster）；这里只做等级差。
    delta = float(pet_level) - float(max(1, int(enemy_level)))

    lo = float(node.get("min_mult", 0.5))
    hi = float(node.get("max_mult", 1.9))
    apply_to = str(node.get("apply_to", "both")).lower()

    def _mult(per_level: float) -> float:
        return max(lo, min(hi, 1.0 + float(per_level) * delta))

    atk_mult = (
        _mult(node.get("atk_per_level", 0.01)) if apply_to in ("both", "atk") else 1.0
    )
    if apply_to in ("both", "def"):
        # 耐打度越高 → 实际承伤越低。取倒数后同样钳制到 [min_mult, max_mult]，
        # 让两个乘区的**语义一致**（都是「倍率区间」），
        # 而不是输出最多 ×1.9、承伤却能到 ×2.0。
        taken_mult = 1.0 / max(1e-6, _mult(node.get("def_per_level", 0.012)))
        taken_mult = max(lo, min(hi, taken_mult))
    else:
        taken_mult = 1.0
    return atk_mult, taken_mult


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
