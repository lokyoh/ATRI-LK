from __future__ import annotations

import time
from dataclasses import dataclass, field

#: 每群每小时最多的主动发言次数。可按群调整，这是默认值。
DEFAULT_MAX_PER_HOUR = 3

#: 两次主动发言之间的最小间隔（秒）。
DEFAULT_COOLDOWN = 300

#: 烦躁达到这个值时，主动发言意愿归零。
IRRITATION_SILENCE_THRESHOLD = 0.6


@dataclass
class _Bucket:
    """单个会话的发言记录。"""

    timestamps: list[float] = field(default_factory=list)

    def prune(self, now: float) -> None:
        cutoff = now - 3600
        self.timestamps = [t for t in self.timestamps if t > cutoff]

    def last_at(self) -> float:
        return max(self.timestamps) if self.timestamps else 0.0


class InitiativeLimiter:
    """主动发言限流器。

    进程内内存即可：这是一份**短期**节流记录，重启后清空是可接受的
    （重启后立刻插话一次，不会造成刷屏）。
    """

    def __init__(
        self,
        *,
        max_per_hour: int = DEFAULT_MAX_PER_HOUR,
        cooldown: float = DEFAULT_COOLDOWN,
    ) -> None:
        self.max_per_hour = max(1, int(max_per_hour))
        self.cooldown = max(0.0, float(cooldown))
        self._buckets: dict[str, _Bucket] = {}

    def _bucket(self, chat_id: str) -> _Bucket:
        key = str(chat_id)
        if key not in self._buckets:
            self._buckets[key] = _Bucket()
        return self._buckets[key]

    def allow(
        self,
        chat_id: str,
        *,
        irritation: float = 0.0,
        now: float | None = None,
    ) -> tuple[bool, str]:
        """能否主动发言。返回 (是否允许, 原因)。

        原因用于日志与调试——排查「她怎么不说话」时需要知道是哪条规则拦的。
        """
        now = time.time() if now is None else now
        bucket = self._bucket(chat_id)
        bucket.prune(now)

        if irritation >= IRRITATION_SILENCE_THRESHOLD:
            return False, f"烦躁度 {irritation:.2f} 过高，她不想说话"

        if len(bucket.timestamps) >= self.max_per_hour:
            return False, f"已达每小时上限 {self.max_per_hour} 次"

        elapsed = now - bucket.last_at()
        if bucket.last_at() and elapsed < self.cooldown:
            return False, f"距上次主动发言仅 {elapsed:.0f}s，冷却中"

        return True, ""

    def record(self, chat_id: str, *, now: float | None = None) -> None:
        """记一次主动发言。只在**真的发出去了**之后调用。"""
        now = time.time() if now is None else now
        bucket = self._bucket(chat_id)
        bucket.prune(now)
        bucket.timestamps.append(now)

    def reset(self, chat_id: str | None = None) -> None:
        """测试用。"""
        if chat_id is None:
            self._buckets.clear()
        else:
            self._buckets.pop(str(chat_id), None)


#: 全局单例。
initiative_limiter = InitiativeLimiter()


def configure(max_per_hour: int, cooldown: float) -> None:
    """按配置重设参数（热重载时调用）。"""
    initiative_limiter.max_per_hour = max(1, int(max_per_hour))
    initiative_limiter.cooldown = max(0.0, float(cooldown))
