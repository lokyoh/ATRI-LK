import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar

_T = TypeVar("_T")
LogListener = Callable[[_T], Awaitable[None]]


class LogStorage(Generic[_T]):
    """
    日志存储
    """

    def __init__(self, rotation: float = 5 * 60, max_logs: int = 100):
        self.count, self.rotation = 0, rotation
        self.max_logs = max_logs
        self.logs: OrderedDict[int, str] = OrderedDict()
        self.listeners: set[LogListener[str]] = set()

    async def add(self, log: str):
        seq = self.count = self.count + 1
        self.logs[seq] = log
        if len(self.logs) > self.max_logs:
            self.logs.popitem(last=False)
        asyncio.get_running_loop().call_later(self.rotation, self.remove, seq)
        await asyncio.gather(
            *(listener(log) for listener in self.listeners),
            return_exceptions=True,
        )
        return seq

    def get_recent(self, limit: int = 100) -> list[str]:
        return list(self.logs.values())[-limit:]

    def remove(self, seq: int):
        self.logs.pop(seq, None)


LOG_STORAGE: LogStorage[str] = LogStorage[str]()
