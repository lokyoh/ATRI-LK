"""Interaction system: the actions that spend the pet's daily action points.

Every function validates its action-point cost against ``daily.yaml`` and
applies its effect from ``interactions.yaml``.  喂食/玩耍/洗澡/休息 的亲密度
收益统一为 **3d10 随机骰**（``interactions.yaml`` 的 ``affection_roll``）。

These actions deliberately write **nothing** to the biography: feeding, playing
and bathing are routine, and ``core.biography`` only keeps biography-worthy
events (``milestone`` / ``key_choice`` / ``battle_victory`` / ``first_time`` /
``ending``).  The life story is assembled by ``core.daily`` and
``core.reincarnation`` instead.
"""

import random
from typing import Any

from .affection import add_affection
from .status import clamp_status, update_emotion_state, update_health_state

_STATUS_FLOOR = 0
"""Interaction effects clamp statuses into ``[0, 100]``."""

_TRAIN_STATUS_FIELDS = ("hunger", "mood", "fatigue")
"""训练前置校验要预演的三项状态（饱食 / 心情 / 疲劳）。

注意：**对外展示**的第三项是「体力」= ``100 - fatigue``（越大越好）；内部字段
仍叫 ``fatigue``（越大越累），因为存量数据、衰减配置、健康判定全都按疲劳的
方向定义。两者只在 :func:`_display_value` 这一处换算，别在别处再做一次反算。
"""

_DEFAULT_TRAIN_REQUIREMENTS: dict[str, int] = {
    "hunger_min": 30,
    "mood_min": 30,
    "fatigue_max": 80,
}
"""训练资源门槛的**最后兜底**，与 ``daily.yaml`` 的照护阈值同口径。

默认值本身来自 ``daily.yaml`` 的 ``affection_decay.thresholds``（单一事实来源），
这里只在两份配置都读不到时生效。
"""


def _load_daily_config() -> dict[str, Any]:
    from ..config_loader import load_config

    return load_config("daily")


def _load_interactions_config() -> dict[str, Any]:
    from ..config_loader import load_config

    return load_config("interactions")


def _action_costs(daily_config: dict[str, Any] | None) -> dict[str, int]:
    cfg = daily_config if daily_config is not None else _load_daily_config()
    return cfg["action_points"]["costs"]


def _mark_interaction(pet, counter: str) -> None:
    """Count one interaction and stamp the personal day it happened on.

    ``stats["last_interaction_day"]`` is kept as a record only; the affection
    decay rule lives in ``core.status`` and keys off unmet care needs.
    """
    pet.stats[counter] = pet.stats.get(counter, 0) + 1
    pet.stats["last_interaction_day"] = pet.age_days


def check_action_point(
    pet,
    action: str,
    *,
    daily_config: dict[str, Any] | None = None,
) -> None:
    """Raise ``ValueError`` when the pet cannot afford ``action``."""
    cost = _action_costs(daily_config).get(action, 0)
    if pet.action_points < cost:
        raise ValueError(f"行动点不足，需要 {cost} 点，当前 {pet.action_points} 点")


def consume_action_point(
    pet,
    action: str,
    *,
    daily_config: dict[str, Any] | None = None,
) -> None:
    """Deduct the action-point cost of ``action``, never going below zero."""
    cost = _action_costs(daily_config).get(action, 0)
    pet.action_points = max(0, pet.action_points - cost)


_TRAIN_LABELS = {
    "hunger": "饱食",
    "mood": "心情",
    "fatigue": "体力",
}
"""训练门槛提示用的中文名。

``fatigue`` 对外叫**体力**：内部是「越大越累」的疲劳值，展示时反算成
「越大越好」的体力（见 :func:`_display_value`）。名字换、方向也换，两者必须
一起改——只换名字会出现「体力 84/100（需 ≤80）」这种读不通的提示。
"""

_DISPLAY_INVERTED = frozenset({"fatigue"})
"""展示方向与内部方向相反的状态字段。"""

_TRAIN_BUTTONS = {
    "hunger": "喂食",
    "mood": "玩耍",
    "fatigue": "休息",
}
"""训练失败时每个不足项对应的**动作名**。

按钮只写动作、不写数值：数值已经在正文的对照行里，按钮再写一遍会让它随每次
状态变化抖动，也更容易让人以为「点一下就能精确补这么多」。
"""

STATUS_MAX = 100
"""状态量程上限（与 ``core.status.STATUS_MAX`` 同值）。"""


def _display_value(field: str, value: int) -> int:
    """把内部状态值换算成**对外展示值**。

    ``fatigue`` 内部 0 = 精神饱满、100 = 精疲力竭；玩家看到的「体力」正好相反。
    """
    return STATUS_MAX - value if field in _DISPLAY_INVERTED else value


def _display_limit(field: str, limit: int) -> int:
    """把内部门槛换算成对外展示的门槛（``fatigue_max`` → 体力下限）。

    换算之后，三项门槛的语义统一成「展示值不得低于这个数」——体力 16 低于
    20，与饱食 22 低于 30 是同一种「不足」，展示层不需要再区分方向。
    """
    return STATUS_MAX - limit if field in _DISPLAY_INVERTED else limit


def _care_thresholds() -> dict[str, int]:
    """``daily.yaml`` 的照护阈值（与好感衰减判定同一份数值）。"""
    try:
        return dict(_load_daily_config()["affection_decay"]["thresholds"])
    except (FileNotFoundError, TypeError, KeyError):
        return {}


def train_requirements(
    interactions_config: dict[str, Any] | None = None,
    *,
    daily_config: dict[str, Any] | None = None,
) -> dict[str, int]:
    """训练的资源门槛：训练**结束后**必须仍然满足的状态区间。

    优先级：``interactions.yaml`` 的 ``effects.train.requirements`` →
    ``daily.yaml`` 的 ``affection_decay.thresholds`` → :data:`_DEFAULT_TRAIN_REQUIREMENTS`。
    默认与好感衰减同口径，所以「练完刚好掉出照护线」也会被拒绝——玩家不会一觉醒来
    平白掉好感；想放宽/收紧只改 ``interactions.yaml`` 那三项即可。
    """
    cfg = (
        interactions_config
        if interactions_config is not None
        else _load_interactions_config()
    )
    effects = cfg.get("effects", {}).get("train", {})
    configured = effects.get("requirements") or {}

    daily_cfg = None
    if daily_config is not None:
        daily_cfg = daily_config.get("affection_decay", {}).get("thresholds", {})
    else:
        daily_cfg = _care_thresholds()

    source = {
        "hunger_min": daily_cfg.get("hunger"),
        "mood_min": daily_cfg.get("mood"),
        "fatigue_max": daily_cfg.get("fatigue"),
    }
    # 用 `is None` 判断而不是 `or`：阈值配成 0（照护线关掉）也是合法值。
    return {
        key: int(
            configured.get(key, source[key] if source[key] is not None else default)
        )
        for key, default in _DEFAULT_TRAIN_REQUIREMENTS.items()
    }


def _train_status_after(pet, effects: dict[str, Any]) -> dict[str, int]:
    """预演训练后的三项状态（与 :func:`train` 的扣减完全同一套 clamp）。"""
    return {
        field: clamp_status(
            getattr(pet, field) + int(effects.get(field, 0)), _STATUS_FLOOR
        )
        for field in _TRAIN_STATUS_FIELDS
    }


class ResourceShortageError(ValueError):
    """训练前的资源不足。

    除了给人看的文字，还带上**结构化数据**，让展示层自己决定怎么排：

    * ``hints``：建议动作 ``[{label, path}]``——QQ 端做成行内/底部按钮，
      OneBot 端由 :func:`reply.plain_with_choices` 渲染成指令清单；
    * ``details``：每一项的实际数值 ``[{label, value, limit, need, over}]``，
      用来画「练后 22/30，还差 8」这种带数值条的对照行。

    **文字里不再写指令**。早先 core 把 ``/宠物.喂食`` 拼进提示语，而底部又给
    一遍同样的按钮，同一句话把「手打」和「点一下」说了两次；现在指令只由
    展示层按适配器生成。
    """

    def __init__(
        self,
        message: str,
        hints: list[dict[str, str]] | None = None,
        details: list[dict[str, Any]] | None = None,
    ):
        super().__init__(message)
        self.hints = hints or []
        self.details = details or []


def check_train_resources(
    pet,
    effects: dict[str, Any],
    requirements: dict[str, int],
) -> None:
    """Raise ``ValueError`` when the pet lacks the resources a training needs.

    训练不是「有行动点就能点」的动作：它要吃掉饱食、压心情、耗体力。
    这里按**训练之后**的值判定，任何一项会越界就整体拒绝，并把缺什么、
    该先做什么写进结构化数据，而不是先扣完行动点再说「状态太差」。

    判定用的是「练完之后」的数值——所以展示层必须标明这一点：玩家当前
    饱食 32 看着够，练完只剩 22，不写清楚会以为门槛算错了。

    ``details`` / ``hints`` 里的数值一律是**对外展示值**（体力方向），内部字段
    仍是 ``fatigue``；换算只在 :func:`_display_value` / :func:`_display_limit`。
    """
    after = _train_status_after(pet, effects)
    details: list[dict[str, Any]] = []
    hints: list[dict[str, str]] = []

    def add(field: str, path: str, limit: int) -> None:
        value = _display_value(field, after[field])
        shown_limit = _display_limit(field, limit)
        # 换算成展示值之后三项语义统一：**展示值不得低于门槛**。
        # 体力 16 低于 20 与饱食 22 低于 30 是同一种「不足」，展示层不必再分方向。
        details.append(
            {
                "label": _TRAIN_LABELS[field],
                "value": value,
                "limit": shown_limit,
                "need": max(0, shown_limit - value),
            }
        )
        # 按钮只写「做什么」，不带数值——数值在正文的对照行里已经有了，
        # 按钮上再写一遍会随每次状态变化抖动，也容易让人以为能精确补这么多。
        hints.append({"label": _TRAIN_BUTTONS[field], "path": path})

    if after["hunger"] < requirements["hunger_min"]:
        add("hunger", "宠物.喂食", requirements["hunger_min"])
    if after["mood"] < requirements["mood_min"]:
        add("mood", "宠物.玩耍", requirements["mood_min"])
    if after["fatigue"] > requirements["fatigue_max"]:
        # 体力 = 100 - 疲劳；疲劳超上限等价于体力跌破下限。
        add("fatigue", "宠物.休息", requirements["fatigue_max"])

    if details:
        raise ResourceShortageError(
            "训练还没开始：状态不够，练完会掉出照护线。",
            hints,
            details,
        )


def _affection_roll(interactions_config: dict[str, Any] | None = None) -> int:
    """互动好感收益：3d10 随机骰（骰子规格外置 interactions.yaml 的 affection_roll）。"""
    cfg = (
        interactions_config
        if interactions_config is not None
        else _load_interactions_config()
    )
    roll = cfg.get("affection_roll", {"count": 3, "sides": 10})
    return sum(random.randint(1, int(roll["sides"])) for _ in range(int(roll["count"])))


def _feed_message(pet) -> str:
    from ..utils.texts import text

    return text("messages.feed", f"{pet.name}开心地吃下了食物", name=pet.name)


async def feed(
    pet,
    *,
    interactions_config: dict[str, Any] | None = None,
    daily_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Feed the pet; restores a fixed hunger amount from ``interactions.yaml``."""
    cfg = (
        interactions_config
        if interactions_config is not None
        else _load_interactions_config()
    )
    effects = cfg["effects"]["feed"]
    gain = int(effects["hunger"])

    check_action_point(pet, "feed", daily_config=daily_config)
    pet.hunger = clamp_status(pet.hunger + gain, _STATUS_FLOOR)
    pet.mood = clamp_status(pet.mood + effects.get("mood", 0), _STATUS_FLOOR)
    roll = _affection_roll(cfg)
    add_affection(pet, roll)
    _mark_interaction(pet, "feed_count")
    pet.daily_flags["fed"] = True
    consume_action_point(pet, "feed", daily_config=daily_config)
    update_emotion_state(pet)
    return {
        "hunger": pet.hunger,
        "mood": pet.mood,
        "affection": pet.affection,
        "affection_gain": roll,
        "action_points": pet.action_points,
        "message": _feed_message(pet),
    }


async def play(
    pet,
    *,
    interactions_config: dict[str, Any] | None = None,
    daily_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Play with the pet; raises mood at the cost of fatigue; 3d10 affection."""
    cfg = (
        interactions_config
        if interactions_config is not None
        else _load_interactions_config()
    )
    effects = cfg["effects"]["play"]
    check_action_point(pet, "play", daily_config=daily_config)
    pet.mood = clamp_status(pet.mood + effects["mood"], _STATUS_FLOOR)
    pet.fatigue = clamp_status(pet.fatigue + effects["fatigue"], _STATUS_FLOOR)
    roll = _affection_roll(cfg)
    add_affection(pet, roll)
    _mark_interaction(pet, "play_count")
    pet.daily_flags["played"] = True
    consume_action_point(pet, "play", daily_config=daily_config)
    update_emotion_state(pet)
    update_health_state(pet)
    return {
        "mood": pet.mood,
        "fatigue": pet.fatigue,
        "affection": pet.affection,
        "affection_gain": roll,
        "action_points": pet.action_points,
        "message": get_play_message(pet),
    }


async def bathe(
    pet,
    *,
    interactions_config: dict[str, Any] | None = None,
    daily_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bathe the pet; raises cleanliness but costs a little mood."""
    cfg = (
        interactions_config
        if interactions_config is not None
        else _load_interactions_config()
    )
    effects = cfg["effects"]["bathe"]
    check_action_point(pet, "bathe", daily_config=daily_config)
    pet.cleanliness = clamp_status(
        pet.cleanliness + effects["cleanliness"], _STATUS_FLOOR
    )
    pet.mood = clamp_status(pet.mood + effects["mood"], _STATUS_FLOOR)
    roll = _affection_roll(cfg)
    add_affection(pet, roll)
    _mark_interaction(pet, "bathe_count")
    pet.daily_flags["bathed"] = True
    consume_action_point(pet, "bathe", daily_config=daily_config)
    update_health_state(pet)
    return {
        "cleanliness": pet.cleanliness,
        "affection": pet.affection,
        "affection_gain": roll,
        "action_points": pet.action_points,
        "message": get_bathe_message(pet),
    }


async def rest(
    pet,
    *,
    interactions_config: dict[str, Any] | None = None,
    daily_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Let the pet sleep; spends action points, recovers fatigue; 3d10 affection."""
    cfg = (
        interactions_config
        if interactions_config is not None
        else _load_interactions_config()
    )
    effects = cfg["effects"]["rest"]
    check_action_point(pet, "rest", daily_config=daily_config)
    roll = _affection_roll(cfg)
    add_affection(pet, roll)
    pet.fatigue = max(0, pet.fatigue - effects["fatigue_recover"])
    pet.mood = clamp_status(pet.mood + effects["mood"], _STATUS_FLOOR)
    _mark_interaction(pet, "rest_count")
    pet.daily_flags["rested"] = True
    consume_action_point(pet, "rest", daily_config=daily_config)
    update_health_state(pet)

    from ..utils.texts import text

    return {
        "fatigue": pet.fatigue,
        "affection": pet.affection,
        "affection_gain": roll,
        # rest 模板含 {action_points}：生产者必须带上扣点后的当前值。
        "action_points": pet.action_points,
        "message": text("messages.rest", f"{pet.name}美美地睡了一觉", name=pet.name),
    }


def get_play_message(pet) -> str:
    from ..utils.texts import text

    if pet.mood >= 80:
        return text("messages.play.happy", f"{pet.name}开心地蹦蹦跳跳", name=pet.name)
    elif pet.mood >= 50:
        return text("messages.play.normal", f"{pet.name}和你玩了一会儿", name=pet.name)
    else:
        return text("messages.play.low", f"{pet.name}有点心不在焉", name=pet.name)


def get_bathe_message(pet) -> str:
    from ..utils.texts import text

    if pet.mood >= 60:
        return text(
            "messages.bathe.willing", f"{pet.name}乖乖地让你洗完了澡", name=pet.name
        )
    else:
        return text(
            "messages.bathe.unwilling", f"{pet.name}不太情愿地洗完了澡", name=pet.name
        )


async def train(
    pet,
    player_id: str | int,
    *,
    interactions_config: dict[str, Any] | None = None,
    daily_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """训练宠物获取经验；消耗行动点和状态，经验根据肉鸽历史最高层数动态调整。

    训练前有两道门：行动点够（``check_action_point``），以及**资源够**
    （``check_train_resources``——练完之后饱食 / 心情 / 疲劳仍须留在照护区间内）。
    两道门都在扣状态与扣点之前判定，拒绝时宠物不会被改动。
    """
    from ..database import load_rogue
    from .leveling import calc_exp_gain, check_level_up

    cfg = (
        interactions_config
        if interactions_config is not None
        else _load_interactions_config()
    )
    effects = cfg["effects"]["train"]
    check_action_point(pet, "train", daily_config=daily_config)
    # 资源门槛：训练要吃掉饱食 / 压心情 / 抬疲劳，练完还得站得住才允许开始。
    # 放在扣行动点之前判定，失败时宠物状态与行动点都不会被动过。
    check_train_resources(pet, effects, train_requirements(cfg))

    # 读取肉鸽历史最高层数
    rogue = load_rogue(player_id)
    best_floor = rogue.best_floor if rogue else 0

    # 根据最高层数匹配经验档位
    exp_tiers = effects.get("exp_tiers", [])
    base_exp = effects.get("exp_base", 500)
    for tier in sorted(exp_tiers, key=lambda t: t.get("floor", 0), reverse=True):
        if best_floor >= tier.get("floor", 0):
            base_exp = tier.get("exp", base_exp)
            break

    # 消耗状态：复用 check_train_resources 的同一个预演函数，判定与实际扣减不会漂移。
    after = _train_status_after(pet, effects)
    pet.hunger = after["hunger"]
    pet.fatigue = after["fatigue"]
    pet.mood = after["mood"]

    # 永久加成（成就点兑换的经验 / 属性）在本指令里读一次，经验与升级共用，
    # 避免同一次训练里两次读库、也避免两处口径不一致。
    from .reincarnation import bonus_for

    permanent_bonus = bonus_for(pet.player_id)

    # 计算经验（带状态修正）
    exp_gain = calc_exp_gain(base_exp, pet, permanent_bonus=permanent_bonus)
    pet.exp += exp_gain

    # 检查升级
    level_result = check_level_up(pet, permanent_bonus=permanent_bonus)

    _mark_interaction(pet, "train_count")
    consume_action_point(pet, "train", daily_config=daily_config)
    update_emotion_state(pet)
    update_health_state(pet)

    return {
        "name": pet.name,
        "exp": exp_gain,
        "level": pet.level,
        "hunger": pet.hunger,
        "fatigue": pet.fatigue,
        "mood": pet.mood,
        "action_points": pet.action_points,
        "level_up": level_result.get("level_up", False),
        "old_level": level_result.get("old_level", pet.level),
        "new_level": level_result.get("new_level", pet.level),
        "best_floor": best_floor,
        "learned_skills": level_result.get("learned_skills", []),
        "message": _train_message(pet, level_result),
    }


def _train_message(pet, level_result: dict) -> str:
    from ..utils.texts import text

    if level_result.get("level_up"):
        lines = [
            text(
                "messages.train.level_up",
                f"{pet.name}升级了！Lv.{level_result['old_level']} → Lv.{level_result['new_level']}",
                name=pet.name,
                old_level=level_result["old_level"],
                new_level=level_result["new_level"],
            )
        ]
    else:
        lines = [text("messages.train.normal", f"{pet.name}完成了训练", name=pet.name)]

    # 升级里程碑发的技能是静默入池/自动装备的，这里必须告诉玩家。
    learned = level_result.get("learned_skills") or []
    if learned:
        names = "、".join(
            str(item.get("name") or item.get("id", "")) for item in learned
        )
        lines.append(text("skill.granted", "📖 学会新技能：{names}", names=names))
        benched = [
            str(item.get("name") or item.get("id", ""))
            for item in learned
            if not item.get("equipped")
        ]
        if benched:
            lines.append(
                text(
                    "skill.granted_full",
                    "📖 新技能 {name} 已进入技能库（装备槽已满，可在 /宠物.技能 中调整）",
                    name="、".join(benched),
                )
            )
    return "\n".join(lines)
