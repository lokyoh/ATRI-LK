from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from ATRI.log import log

#: 状态一半复位所需的时间（秒）。mood / irritation 都按这个节奏回归。
#: 30 分钟：够长到能延续一整段对话，够短到不会「记仇一整天」。
HALF_LIFE_SECONDS = 30 * 60

#: 单个情绪分量的绝对值上限，防止连续事件把它推到荒谬的极端。
MAX_MAGNITUDE = 1.0

#: 情绪强度低于这个值就当成「没什么感觉」，避免注入无意义的噪声。
NEUTRAL_EPSILON = 0.15


def _now() -> float:
    return time.time()


def _decay_to_baseline(value: float, elapsed: float) -> float:
    """把 value 朝 0 拉，经过 elapsed 秒。

    用半衰期而不是线性，是因为情绪的自然消退是指数的：
    刚吵完很气，十分钟后缓和，一小时后就只剩一点余味。
    """
    if elapsed <= 0 or value == 0.0:
        return value
    factor = math.pow(0.5, elapsed / HALF_LIFE_SECONDS)
    return value * factor


@dataclass
class InnerState:
    """她此刻的内心状态。

    字段都是**当下**的感受，不承载长期评价（那是 ``User.love`` 的事）。
    """

    mood: float = 0.0
    """-1.0 沮丧 ~ 1.0 愉快。"""

    irritation: float = 0.0
    """0.0 ~ 1.0 被冒犯 / 被反复打扰的烦躁。"""

    fatigue: float = 0.0
    """0.0 ~ 1.0 疲倦，由作息时段驱动。"""

    attention: str = ""
    """她此刻在做什么（来自日程的当前时段），空串表示未设置。"""

    updated_at: float = field(default_factory=_now)

    def settle(self, now: float | None = None) -> InnerState:
        """把状态推进到 now：情绪回归、疲劳只由外部重算不自行衰减。"""
        now = _now() if now is None else now
        elapsed = max(0.0, now - self.updated_at)
        self.mood = _decay_to_baseline(self.mood, elapsed)
        self.irritation = _decay_to_baseline(self.irritation, elapsed)
        self.updated_at = now
        return self

    def nudge(
        self,
        *,
        mood: float = 0.0,
        irritation: float = 0.0,
        now: float | None = None,
    ) -> InnerState:
        """施加一次情绪冲击。先结算，再叠加，再钳位。"""
        self.settle(now)
        if mood:
            room = MAX_MAGNITUDE - abs(self.mood)
            self.mood += mood * room
        if irritation:
            room = MAX_MAGNITUDE - abs(self.irritation)
            self.irritation += irritation * room
        self.mood = max(-MAX_MAGNITUDE, min(MAX_MAGNITUDE, self.mood))
        self.irritation = max(0.0, min(MAX_MAGNITUDE, self.irritation))
        return self

    @property
    def is_neutral(self) -> bool:
        return (
            abs(self.mood) < NEUTRAL_EPSILON
            and self.irritation < NEUTRAL_EPSILON
            and self.fatigue < NEUTRAL_EPSILON
        )

    def describe(self) -> str:
        """渲染成一小段自然语言，给提示词用。

        刻意写得**克制、不含指令**：只说"你现在有点烦"，不写"所以你应该用短句"。
        怎么表达是模型的判断，硬编码行为规则会让角色变得机械。
        """
        parts: list[str] = []
        if self.fatigue >= 0.6:
            parts.append("你已经很累了，注意力有点涣散")
        elif self.fatigue >= 0.3:
            parts.append("你有些疲倦")
        if self.irritation >= 0.5:
            parts.append("你正在为刚才的事不高兴，不想太热情")
        elif self.irritation >= 0.25:
            parts.append("你有点不耐烦")
        if self.mood <= -0.35:
            parts.append("你心情不太好")
        elif self.mood >= 0.35:
            parts.append("你心情不错")
        if self.attention:
            parts.append(f"你此刻正在{self.attention}")
        if not parts:
            return ""
        return "；".join(parts) + "。"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> InnerState:
        data = data or {}
        return cls(
            mood=float(data.get("mood", 0.0) or 0.0),
            irritation=float(data.get("irritation", 0.0) or 0.0),
            fatigue=float(data.get("fatigue", 0.0) or 0.0),
            attention=str(data.get("attention", "") or ""),
            updated_at=float(data.get("updated_at", 0.0) or _now()),
        )


class StateStore:
    """把状态存在 ``USERDATA`` 的一个保留 ID 下。

    复用 ``user_table`` 而不是新开一张表：状态是单行小对象，
    为它建表、写迁移不值当。保留 ID 与 ``SELF_MEMORY_ID`` 同级，
    不会与 QQ 号冲突。
    """

    KEY = "__atri_state__"

    def __init__(self) -> None:
        self._cache: InnerState | None = None

    def _table(self):
        from .database import user_table

        return user_table

    def load(self) -> InnerState:
        if self._cache is not None:
            return self._cache
        try:
            rows = self._table().select("DATA", f"ID='{self.KEY}'")
            if rows:
                self._cache = InnerState.from_dict(json.loads(rows[0][0]))
            else:
                self._cache = InnerState()
        except Exception as exc:
            log.warning(f"读取内心状态失败，回退为中性：{exc}")
            self._cache = InnerState()
        return self._cache

    def save(self, state: InnerState) -> None:
        state.settle()
        self._cache = state
        try:
            payload = json.dumps(state.to_dict(), ensure_ascii=False)
            table = self._table()
            if table.select("DATA", f"ID='{self.KEY}'"):
                table.update((("DATA",), (payload,)), f"ID='{self.KEY}'")
            else:
                table.insert("ID, DATA", (self.KEY, payload))
        except Exception as exc:
            log.warning(f"保存内心状态失败：{exc}")

    def reset_cache(self) -> None:
        """测试/重载用。"""
        self._cache = None


state_store = StateStore()


def get_state() -> InnerState:
    return state_store.load()


def feel(
    mood: float = 0.0,
    irritation: float = 0.0,
    *,
    persist: bool = True,
) -> InnerState:
    """施加一次情绪冲击并（默认）落盘。

    注意签名里**没有 love / 好感度**：情绪与关系评价是两条独立通路（B 方案）。
    """
    state = state_store.load()
    state.nudge(mood=mood, irritation=irritation)
    if persist:
        state_store.save(state)
    return state


def set_fatigue(value: float, attention: str | None = None) -> InnerState:
    """更新疲劳与「此刻在做什么」。由日程/时段驱动，不由对话驱动。"""
    state = state_store.load()
    state.settle()
    state.fatigue = max(0.0, min(MAX_MAGNITUDE, float(value)))
    if attention is not None:
        state.attention = str(attention)
    state_store.save(state)
    return state


def fatigue_for_hour(hour: int) -> float:
    """按小时给一个基础疲劳值。

    对应 ``schedule.py`` 里「02:00–06:00 睡眠」的设定。曲线必须**醒来后单调递增**：
    她 6 点起床，所以清早最清醒、深夜最累。
    （第一版把 6–9 点写成 0.35，比午后 0.3 还高——那是倒挂的：
    刚睡醒的人不可能比上了一天班的人更累。）
    """
    if 2 <= hour < 6:
        return 0.9  # 本该在睡觉
    if hour < 9:
        return 0.1  # 刚醒，最清醒
    if hour < 12:
        return 0.2
    if hour < 18:
        return 0.35
    if hour < 23:
        return 0.5
    return 0.75  # 23:00–02:00 深夜


SETTLE_BLEND = 0.25


def settle_from_turn(
    *,
    replied: bool,
    rounds: int,
    user_was_master: bool = False,
    now_ts: float | None = None,
) -> InnerState:
    """每轮对话结束后**被动结算**情绪——不依赖模型主动调 ``feel``。

    为什么需要这个：``thinking.py`` 的系统提示词要求「不写情绪描写」，
    而 ``feel`` 是模型表达情绪的唯一入口。两者叠加成一个死循环——
    模型被要求克制 → 很少调 feel → 状态长期停在基线 → 状态注入形同虚设，
    ``telemetry.feel`` 也因此几乎没有采样，「情绪是否长期极端」根本测不出来。

    这里改用「由已发生的交互事实推导情绪」：
    她说了话、说了几轮、对谁说的——这些本来就有，不需要模型额外表态。

    刻意保守：每次只把情绪往目标值拉 ``SETTLE_BLEND``，且幅度都很小。
    情绪应该是被**慢慢带动**的，不该因为一轮对话就翻脸或狂喜。
    """
    now_ts = _now() if now_ts is None else now_ts
    state = state_store.load()
    state.settle(now_ts)

    target_mood = 0.0
    if replied:
        target_mood += 0.08 * min(3, max(0, rounds))
    if user_was_master and replied:
        target_mood += 0.05

    state.mood += (target_mood - state.mood) * SETTLE_BLEND
    state.mood = max(-MAX_MAGNITUDE, min(MAX_MAGNITUDE, state.mood))
    state_store.save(state)
    return state
