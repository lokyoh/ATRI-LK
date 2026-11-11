import time
import uuid
from collections import deque
from dataclasses import dataclass

from ATRI.log import log

from .reason import BlockReason


@dataclass(frozen=True)
class AuthTrace:
    trace_id: str
    created_at: float
    decision: str
    stage: str
    reason: str | None
    service: str | None
    bot_id: str
    user_id: str
    group_id: str

    def as_log(self) -> str:
        return (
            f"trace={self.trace_id} decision={self.decision} "
            f"stage={self.stage} reason={self.reason or '-'} "
            f"service={self.service or '-'} bot_id={self.bot_id} "
            f"user_id={self.user_id or '-'} group_id={self.group_id or '-'}"
        )


class TraceBuffer:
    """固定容量的环形缓冲。"""

    def __init__(self, capacity: int = 500):
        self._items: deque[AuthTrace] = deque(maxlen=max(1, capacity))

    @property
    def capacity(self) -> int:
        return self._items.maxlen or 0

    def configure(self, capacity: int) -> None:
        """调整容量；已有记录保留。"""
        capacity = max(1, capacity)
        if capacity != self._items.maxlen:
            self._items = deque(self._items, maxlen=capacity)

    def record(
        self,
        *,
        decision: str,
        stage: str,
        reason: BlockReason | None,
        service: str | None,
        bot_id: str,
        user_id: str,
        group_id: str,
    ) -> AuthTrace:
        entry = AuthTrace(
            trace_id=uuid.uuid4().hex[:12],
            created_at=time.time(),
            decision=decision,
            stage=stage,
            reason=reason.value if reason is not None else None,
            service=service,
            bot_id=bot_id,
            user_id=user_id,
            group_id=group_id,
        )
        self._items.append(entry)
        if decision == "deny":
            log.info(f"auth deny: {entry.as_log()}")
        else:
            log.debug(f"auth allow: {entry.as_log()}")
        return entry

    def recent(self, limit: int = 50) -> list[AuthTrace]:
        if limit <= 0:
            return []
        return list(self._items)[-limit:]

    def clear(self) -> None:
        self._items.clear()


trace_buffer = TraceBuffer()
