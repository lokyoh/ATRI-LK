import time
from collections import deque
from dataclasses import dataclass, field

import anyio


def now() -> float:
    """当前时间（秒）。单独抽出来便于测试替换。"""
    return time.monotonic()


Key = tuple[str, str, str]


class SlidingWindowCounter:
    """滑动窗口计次。"""

    def __init__(self) -> None:
        self._hits: dict[Key, deque[float]] = {}

    def _prune(self, key: Key, window: float, current: float) -> deque[float]:
        hits = self._hits.setdefault(key, deque())
        cutoff = current - window
        while hits and hits[0] <= cutoff:
            hits.popleft()
        return hits

    def can_hit(
        self,
        service: str,
        scope: str,
        target: str,
        window: float,
        limit: int,
        *,
        current: float | None = None,
    ) -> bool:
        """还能再记一次吗（只检查，不记录）。"""
        if limit <= 0 or window <= 0 or not target:
            return True
        hits = self._prune(
            (service, scope, target), window, now() if current is None else current
        )
        return len(hits) < limit

    def hit(
        self,
        service: str,
        scope: str,
        target: str,
        *,
        current: float | None = None,
    ) -> None:
        """记录一次调用。"""
        if not target:
            return
        stamp = now() if current is None else current
        self._hits.setdefault((service, scope, target), deque()).append(stamp)

    def push(
        self,
        service: str,
        scope: str,
        target: str,
        window: float,
        *,
        current: float | None = None,
    ) -> int:
        """记录一次并返回窗口内的次数。"""
        if not target:
            return 0
        stamp = now() if current is None else current
        hits = self._prune((service, scope, target), window, stamp)
        hits.append(stamp)
        return len(hits)

    def clear(self, service: str, scope: str, target: str) -> None:
        """丢弃单个键的计数。"""
        self._hits.pop((service, scope, target), None)

    def reset(self, service: str | None = None) -> None:
        if service is None:
            self._hits.clear()
            return
        for key in [k for k in self._hits if k[0] == service]:
            self._hits.pop(key, None)


class CooldownStore:
    """冷却时间记录。"""

    def __init__(self) -> None:
        self._last: dict[Key, float] = {}

    def ready(
        self,
        service: str,
        scope: str,
        target: str,
        cd: float,
        *,
        current: float | None = None,
    ) -> bool:
        """距离上次调用是否已超过冷却时间。"""
        if cd <= 0 or not target:
            return True
        last = self._last.get((service, scope, target))
        if last is None:
            return True
        return (now() if current is None else current) - last >= cd

    def touch(
        self,
        service: str,
        scope: str,
        target: str,
        *,
        current: float | None = None,
    ) -> None:
        if not target:
            return
        self._last[(service, scope, target)] = now() if current is None else current

    def reset(self, service: str | None = None) -> None:
        if service is None:
            self._last.clear()
            return
        for key in [k for k in self._last if k[0] == service]:
            self._last.pop(key, None)


@dataclass
class _Slot:
    limit: int
    semaphore: anyio.Semaphore = field(init=False)

    def __post_init__(self) -> None:
        self.semaphore = anyio.Semaphore(self.limit, max_value=self.limit)


class ConcurrencyStore:
    """并发槽位。

    用 ``anyio.Semaphore`` 而不是 ``asyncio.Semaphore``，避免在导入期
    绑定 event loop；槽位按 ``(服务名, 目标 id)`` 隔离。
    """

    def __init__(self) -> None:
        self._slots: dict[tuple[str, str], _Slot] = {}

    def _slot(self, service: str, target: str, limit: int) -> _Slot:
        key = (service, target)
        slot = self._slots.get(key)
        if slot is None or slot.limit != limit:
            # 限制值被改过：换一个新信号量，旧的在途调用 release 时不会再影响新值
            slot = _Slot(limit)
            self._slots[key] = slot
        return slot

    def try_acquire(self, service: str, target: str, limit: int) -> bool:
        """尝试占用一个槽位；``limit <= 0`` 表示不限制。"""
        if limit <= 0 or not target:
            return True
        try:
            self._slot(service, target, limit).semaphore.acquire_nowait()
        except anyio.WouldBlock:
            return False
        return True

    def release(self, service: str, target: str) -> None:
        slot = self._slots.get((service, target))
        if slot is None:
            return
        if slot.semaphore.value < slot.semaphore.max_value:
            slot.semaphore.release()

    def reset(self, service: str | None = None) -> None:
        if service is None:
            self._slots.clear()
            return
        for key in [k for k in self._slots if k[0] == service]:
            self._slots.pop(key, None)


counter = SlidingWindowCounter()
cooldown = CooldownStore()
concurrency = ConcurrencyStore()
malicious = SlidingWindowCounter()

MALICIOUS_SCOPE = "malicious"


async def note_malicious(
    user_id: str,
    *,
    window: float,
    max_count: int,
    ban_minutes: float,
    reason: str = "恶意触发",
    operator_id: str = "ATRI",
    current: float | None = None,
) -> bool:
    """记录一次触发；达到阈值时写入临时封禁并返回 ``True``。

    计数只在内存里，封禁记录落 ``auth_ban_record``（``source="malicious"``）。
    封禁成功后清空该用户的计数，解封后重新开始。
    """
    if not user_id or window <= 0 or max_count <= 0:
        return False
    hits = malicious.push(MALICIOUS_SCOPE, "user", user_id, window, current=current)
    if hits < max_count:
        return False
    malicious.clear(MALICIOUS_SCOPE, "user", user_id)

    from . import ban

    await ban.ban(
        ban.TARGET_USER,
        user_id,
        minutes=ban_minutes,
        reason=reason,
        operator_id=operator_id,
        source=ban.SOURCE_MALICIOUS,
    )
    return True


def reset_all() -> None:
    """清空所有运行时限制（测试或手动重置时使用）。"""
    counter.reset()
    cooldown.reset()
    concurrency.reset()
    malicious.reset()
