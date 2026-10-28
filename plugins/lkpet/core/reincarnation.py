"""Reincarnation system: achievement points, perks and the cycle reset.

A cycle only ends when the player explicitly asks for it and confirms: the daily
settlement may *report* that reincarnation is available, but nothing here runs
automatically.  Biography writes are injected as an optional callback instead of
importing ``core.biography``, which is Step 11's deliverable.
"""

from datetime import datetime
from typing import Any

from ..utils.callbacks import Callback, run_callback
from .attributes import ATTRIBUTE_KEYS
from .leveling import calc_exp_next
from .skills import draw_reincarnation_skills, retention_skill_ids

_PER_ATTR_STEP = 100
"""Attribute points per ``per_100_attr`` weight."""

_PER_AFFECTION_STEP = 200
"""Affection scale is 0-1000, so each weight step is 200 points."""

_PER_DAYS_STEP = 10
"""Days per ``per_10_days`` weight."""

_DEFAULT_LEVEL_CURVE = "quadratic"
"""``per_level`` 写成映射时的默认曲线：累计分随等级**平方**增长。

平方意味着「每级收益越往后越高」——第 100 级单独贡献约 80 分，
而第 1 级几乎为 0。这样轮回得分集中在「把等级练上去」这件事上，
与商店「贵的都是长期目标」的定位一致。
"""


def _load_reincarnation_config() -> dict[str, Any]:
    from ..config_loader import load_config

    return load_config("reincarnation")


def _load_pet_config() -> dict[str, Any]:
    from ..config_loader import load_config

    return load_config("pet")


def _persist(reinc) -> None:
    from ..database import save_reincarnation

    save_reincarnation(reinc)


def calc_level_points(
    level: int,
    weights: dict[str, Any],
) -> int:
    """等级贡献的成就点。

    ``per_level`` 支持两种写法：

    * **数字**（旧配置）：线性，``level × per_level``。
    * **映射**：``{max_total, max_level, curve}``，累计分在 ``max_level`` 时
      恰好等于 ``max_total``，``curve`` 决定曲率：

      ==========  ==========================  ==============================
      curve       累计公式                     每级边际
      ==========  ==========================  ==============================
      linear      ``max_total × r``            恒定
      quadratic   ``max_total × r²``           递增（默认）
      cubic       ``max_total × r³``           递增更快
      ==========  ==========================  ==============================

      其中 ``r = min(1, level / max_level)``。
    """
    raw = weights.get("per_level", 0)
    level = max(0, int(level))

    if isinstance(raw, int | float):
        # 旧配置：线性。保留它是为了老存档 / 未升级的配置文件不报错。
        return max(0, int(level * float(raw)))

    if not isinstance(raw, dict):
        return 0

    try:
        max_total = float(raw.get("max_total", 0) or 0)
        max_level = int(raw.get("max_level", 100) or 100)
    except (TypeError, ValueError):
        return 0
    if max_total <= 0 or max_level <= 0:
        return 0

    curve = str(raw.get("curve", _DEFAULT_LEVEL_CURVE) or _DEFAULT_LEVEL_CURVE)
    ratio = min(1.0, max(0.0, level / max_level))

    if curve == "linear":
        return max(0, int(max_total * ratio))
    if curve == "cubic":
        return max(0, int(max_total * ratio**3))
    # quadratic 是默认，也是无法识别曲线名时的兜底。
    return max(0, int(max_total * ratio**2))


def calc_reincarnation_points(
    pet,
    reincarnation_config: dict[str, Any] | None = None,
) -> int:
    """Score a life: level, attributes, skills, affection and age."""
    cfg = (
        reincarnation_config
        if reincarnation_config is not None
        else _load_reincarnation_config()
    )
    weights = cfg["point_weights"]

    points = calc_level_points(pet.level, weights)

    attr_sum = sum(pet.attributes.get(key, 0) for key in ATTRIBUTE_KEYS)
    points += (attr_sum // _PER_ATTR_STEP) * weights["per_100_attr"]

    # 按「已掌握」的技能数计分（收集池越大分越高，装备与否不影响）。
    skills = pet.skills or {}
    skill_count = len(skills.get("active", [])) + len(skills.get("passive", []))
    points += skill_count * weights["per_skill"]

    points += (pet.affection // _PER_AFFECTION_STEP) * weights["per_200_affection"]
    points += (pet.age_days // _PER_DAYS_STEP) * weights["per_10_days"]

    if pet.age_days >= pet.lifespan_days:
        points += weights["full_lifespan_bonus"]

    return max(1, points)


async def check_reincarnation_available(
    pet,
    reincarnation_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Report whether the pet *may* reincarnate; never performs it."""
    cfg = (
        reincarnation_config
        if reincarnation_config is not None
        else _load_reincarnation_config()
    )
    reasons = []
    if pet.age_days >= pet.lifespan_days:
        reasons.append("寿命已尽")
    if pet.level >= cfg["max_level"]:
        reasons.append("已达等级上限")
    if pet.level >= cfg["min_level_for_manual"]:
        reasons.append("可主动轮回")

    return {
        "can_reincarnate": bool(reasons),
        "reasons": reasons,
        "estimated_points": calc_reincarnation_points(pet, cfg),
    }


async def perform_reincarnation(
    pet,
    player_id: str | int,
    manual: bool = False,
    *,
    reincarnation_config: dict[str, Any] | None = None,
    pet_config: dict[str, Any] | None = None,
    biography_hook: Callback | None = None,
) -> dict[str, Any]:
    """End the current life and start the next one.

    Only call this after the player has explicitly confirmed; the daily
    settlement must never reach it.
    """
    cfg = (
        reincarnation_config
        if reincarnation_config is not None
        else _load_reincarnation_config()
    )
    if manual and pet.level < cfg["min_level_for_manual"]:
        # 等级门槛只拦「提前主动轮回」。寿命已尽或已满级属于自然终结，
        # 必须放行，否则低等级宠物在寿命结束后会永远无法轮回。
        natural_ending = (
            pet.age_days >= pet.lifespan_days or pet.level >= cfg["max_level"]
        )
        if not natural_ending:
            raise ValueError(f"主动轮回需要至少 {cfg['min_level_for_manual']} 级")

    points = calc_reincarnation_points(pet, cfg)
    ending = generate_ending(pet)
    # 「满级轮回」按结束时（重置前）的等级判定，必须在本世数据被清空之前取样。
    ended_at_max_level = pet.level >= int(cfg.get("max_level", 100))
    final_skill_count = len((pet.skills or {}).get("active", [])) + len(
        (pet.skills or {}).get("passive", [])
    )

    await run_callback(
        biography_hook,
        pet,
        {
            "type": "ending",
            "title": f"第 {pet.cycle_count + 1} 世终结",
            "content": ending,
        },
    )

    reinc = await get_or_create_reincarnation(player_id)

    reinc.past_pets.append(
        {
            "name": pet.name,
            "level": pet.level,
            "age_days": pet.age_days,
            "cycle": pet.cycle_count + 1,
            "ending": ending,
            "points_earned": points,
            "skill_count": final_skill_count,
            "finalized_at": datetime.now().isoformat(),
        }
    )
    reinc.achievement_points += points
    reinc.total_cycles += 1
    if pet.level > reinc.best_level:
        reinc.best_level = pet.level
        reinc.best_attributes = dict(pet.attributes)
    if pet.age_days > reinc.best_age_days:
        reinc.best_age_days = pet.age_days

    reset_pet_for_reincarnation(pet, reinc, pet_config=pet_config)
    # 起始等级已在 reset 里用掉，「转世起点」这类一次性商品到此清零。
    # **必须晚于 reset**：先清再重置的话，本次轮回就拿不到起始等级了。
    consumed_start_level = consume_start_level(reinc)
    # 保留名单要在抽新技能之前取，否则刚抽到的轮回技能会在回执里重复出现。
    kept = retention_skill_ids(pet)
    # 满级轮回按配置额外抽 ``grants.reincarnation_draw`` 个轮回技能（池空则少抽）。
    drawn_skills = draw_reincarnation_skills(pet) if ended_at_max_level else []
    pet.cycle_count = reinc.total_cycles
    # 每完成一次轮回赠送洗点券（数量见 reincarnation.yaml 的 shop.respec.free_per_cycle）。
    from .shop import grant_respec_tickets

    grant_respec_tickets(reinc)
    reinc.updated_at = datetime.now()
    _persist(reinc)

    return {
        "points_earned": points,
        "total_points": reinc.achievement_points,
        "cycle": reinc.total_cycles,
        "ending": ending,
        "max_level_cycle": ended_at_max_level,
        "drawn_skill": drawn_skills[0] if drawn_skills else None,
        "drawn_skills": drawn_skills,
        "kept_skills": kept,
        "start_level": pet.level,
        "consumed_perks": consumed_start_level,
    }


def reset_pet_for_reincarnation(
    pet,
    reinc,
    *,
    pet_config: dict[str, Any] | None = None,
) -> None:
    """Put the pet back to the start of a new life, keeping the achievements.

    技能按新规则裁剪：**只保留轮回技能与肉鸽奖励技能**（见
    ``core.skills.apply_reincarnation_retention``），随后补发开局技能并按新等级
    补齐等级里程碑；常规池技能与旧存档技能都会清空。
    """
    from .attributes import calc_attributes_on_level_up
    from .skills import (
        apply_reincarnation_retention,
        ensure_skill_storage,
        grant_level_skills,
        grant_start_skills,
    )

    pet.level = 1 + _pending_start_level_bonus(reinc)
    pet.exp = 0
    # 起始等级可能被特权抬高，exp_next 必须跟着等级走，不能固定写 100。
    pet.exp_next = calc_exp_next(pet.level)
    pet.age_days = 0
    pet.current_day = 0
    pet.last_settlement_day = 0
    pet.cycle_started_at = datetime.now()
    pet.mood = 80
    pet.hunger = 80
    pet.fatigue = 0
    pet.cleanliness = 100
    pet.affection = 0
    pet.health_state = "healthy"
    pet.emotion_state = "normal"
    ensure_skill_storage(pet)
    apply_reincarnation_retention(pet)
    grant_start_skills(pet)
    # 起始等级可能直接跨过若干里程碑（起始等级特权），这里一次补齐。
    grant_level_skills(pet)
    pet.talent_points = 0
    pet.talents = {}
    pet.trained_attributes = {}
    pet.action_points = 10
    pet.daily_flags = {}
    pet.is_alive = True

    cfg = pet_config if pet_config is not None else _load_pet_config()
    # 用手里这份 ``reinc`` 的加成，不再读库：轮回流程里刚写过 permanent_bonuses，
    # 再读一次既浪费又可能在并发下拿到旧值。
    #
    # 起始等级已经在上面**直接写进 pet.level** 了，所以这里要把
    # ``start_level_bonus`` 摘掉——``calc_attributes_on_level_up`` 只认平值属性键，
    # 本来也不会读它，但显式剔除能让"这个键不是属性加成"这件事在代码里可见，
    # 免得以后有人误以为它该留在这里。
    bonus = {
        key: value
        for key, value in (reinc.permanent_bonuses or {}).items()
        if key != "start_level_bonus"
    }
    pet.attributes = calc_attributes_on_level_up(pet, cfg, permanent_bonus=bonus)


def _pending_start_level_bonus(reinc) -> int:
    """本次轮回可用的起始等级加成。

    两个来源，**都只对这一次轮回生效**：

    * 商店里 ``consume_on: reincarnation`` 的商品（如「转世起点」）——
      买下的等级挂在 ``perk_levels``，用掉后由 :func:`consume_start_level` 清零；
    * 旧版 ``perks`` 的 ``start_level_bonus`` —— 它写在 ``permanent_bonuses`` 里，
      没有"消耗"概念，为了不让老玩家凭空失去，仍按原样计入。

    两处相加，因此老存档买了旧特权、又买了新商店商品时不会互相覆盖。
    """
    from .shop import pending_reincarnation_bonus

    bonus = int(reinc.permanent_bonuses.get("start_level_bonus", 0) or 0)
    try:
        pending = pending_reincarnation_bonus(reinc.perk_levels or {})
    except (FileNotFoundError, TypeError, ValueError):
        pending = {}
    return bonus + int(pending.get("start_level_bonus", 0) or 0)


def consume_start_level(reinc) -> dict[str, int]:
    """清空「转世起点」这类一次性商品，返回被消耗掉的等级。

    必须在**起始等级已经用掉之后**调用，否则本次轮回会拿不到加成。
    """
    from .shop import consume_reincarnation_entries

    try:
        remaining, consumed = consume_reincarnation_entries(reinc.perk_levels or {})
    except (FileNotFoundError, TypeError, ValueError):
        return {}
    if not consumed:
        return {}
    reinc.perk_levels = remaining
    return {key: int(value) for key, value in consumed.items()}


def generate_ending(pet) -> str:
    """Describe how the finished life ended."""
    if pet.age_days >= pet.lifespan_days:
        return f"{pet.name}安详地闭上了眼睛，它的一生充满了冒险与陪伴。"
    elif pet.level >= 100:
        return f"{pet.name}达到了巅峰，成为传说中的存在。"
    else:
        return f"{pet.name}选择了主动轮回，期待下一次相遇。"


async def get_or_create_reincarnation(player_id: str | int):
    """Return the player's reincarnation row, creating it with defaults if new."""
    from ..database import Reincarnation, load_reincarnation, save_reincarnation

    reinc = load_reincarnation(str(player_id))
    if reinc is None:
        reinc = Reincarnation(player_id=str(player_id))
        save_reincarnation(reinc)
    return reinc


async def get_permanent_bonus(player_id: str | int) -> dict[str, Any]:
    """Return the permanent bonuses unlocked by spent achievement points."""
    reinc = await get_or_create_reincarnation(player_id)
    return reinc.permanent_bonuses


def bonus_for(player_id: str | int) -> dict[str, int | float]:
    """**同步**读取永久加成（成就点兑换的效果）。

    消费者（:func:`core.leveling.calc_exp_gain` /
    :func:`core.leveling.check_level_up` /
    :func:`core.attributes.calc_attributes_on_level_up`）全部是同步函数，
    不能 await，所以这里走同步的 :func:`database.load_reincarnation`。
    异步版 :func:`get_permanent_bonus` 会在不存在时建行，不适合放在热路径上。

    读不到时返回空 dict：未轮回过的玩家、以及 ``player_id`` 为脏数据的情况
    都等同于「没有任何永久加成」，调用方因此不需要判空。
    """
    from ..database import load_reincarnation

    try:
        reinc = load_reincarnation(player_id)
    except (TypeError, ValueError):
        # 历史数据里可能出现非数字的 player_id（非数字字符串无法 int()）。
        # 一条脏数据不该让喂食 / 训练 / 肉鸽结算整条指令失败。
        return {}
    if reinc is None:
        return {}
    return dict(reinc.permanent_bonuses or {})


def refresh_pet_attributes(
    pet,
    permanent_bonus: dict[str, int | float] | None = None,
    *,
    pet_config: dict[str, Any] | None = None,
) -> None:
    """按永久加重重算 ``pet.attributes``（**只改内存，不落库**）。

    属性是按等级 / 资质 / 年龄 / 天赋派生出来的，买完加成后必须整表重算，
    否则玩家要等到下次升级才看得见。调用方负责随后 ``save_pet``。
    """
    from .attributes import calc_attributes_on_level_up

    bonus = permanent_bonus if permanent_bonus is not None else bonus_for(pet.player_id)
    cfg = pet_config if pet_config is not None else _load_pet_config()
    pet.attributes = calc_attributes_on_level_up(pet, cfg, permanent_bonus=bonus)


async def spend_achievement_points(
    player_id: str | int,
    perk_id: str,
    *,
    reincarnation_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Exchange achievement points for a permanent perk."""
    cfg = (
        reincarnation_config
        if reincarnation_config is not None
        else _load_reincarnation_config()
    )
    perk = cfg["perks"].get(perk_id)
    if not perk:
        raise ValueError("特权不存在")

    reinc = await get_or_create_reincarnation(player_id)
    available = reinc.achievement_points - reinc.spent_points
    if available < perk["cost"]:
        raise ValueError(f"成就点不足，需要 {perk['cost']}，可用 {available}")

    reinc.spent_points += perk["cost"]
    reinc.unlocked_perks.append(perk_id)
    for key, value in perk["effect"].items():
        reinc.permanent_bonuses[key] = reinc.permanent_bonuses.get(key, 0) + value
    reinc.updated_at = datetime.now()
    _persist(reinc)

    return {
        "perk": perk_id,
        "remaining_points": reinc.achievement_points - reinc.spent_points,
    }
