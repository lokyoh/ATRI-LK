import asyncio
import contextlib
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

BOOT_ID = uuid.uuid4().hex[:12]
"""本次进程运行标识：后端重启后变化，前端据此判断断点续传是否有效"""


@dataclass
class LogEntry:
    """一条推送给 WebUI 的日志记录"""

    time: datetime
    level: str
    """loguru 级别名（INFO / WARNING / ERROR / ...），前端不再靠正则猜"""
    text: str
    """带 ANSI 颜色的整行日志，控制台彩色渲染用"""
    seq: int = 0


LogListener = Callable[[LogEntry], None]


class LogStorage:
    """
    日志存储

    - 定长环形缓冲（OrderedDict），超出上限直接淘汰最旧；
    - 每条日志附带过期时间，由事件循环定时器回收；
    - 监听器为同步回调：内部只做入队这类瞬时操作，
      通知路径零 task 创建、零 await，慢客户端由端点侧队列兜底。
    """

    def __init__(self, rotation: float = 5 * 60, max_logs: int = 200):
        self.rotation = rotation
        self.max_logs = max_logs
        self.logs: OrderedDict[int, LogEntry] = OrderedDict()
        self.listeners: set[LogListener] = set()
        self._seq = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: int | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """启动时绑定主事件循环：其他线程打日志时经它转投，保证通知线程安全"""
        self._loop = loop
        self._loop_thread = threading.get_ident()

    def add(self, entry: LogEntry) -> int:
        self._seq += 1
        entry.seq = self._seq
        self.logs[entry.seq] = entry
        if len(self.logs) > self.max_logs:
            self.logs.popitem(last=False)
        # 无事件循环（如其他线程打日志）时跳过定时回收，容量淘汰兜底
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().call_later(self.rotation, self.remove, entry.seq)
        self._notify(entry)
        return entry.seq

    def _notify(self, entry: LogEntry) -> None:
        # 打日志的线程可能不是事件循环线程，而监听器里的 asyncio.Queue
        # 并非线程安全（直接跨线程 put 会丢 sender 唤醒），此时必须转投主循环
        if self._loop is not None and threading.get_ident() != self._loop_thread:
            for listener in tuple(self.listeners):
                # 主循环已关闭（如进程退出）时转投失败，丢弃即可
                with contextlib.suppress(RuntimeError):
                    self._loop.call_soon_threadsafe(
                        self._call_listener, listener, entry
                    )
            return
        for listener in tuple(self.listeners):
            self._call_listener(listener, entry)

    @staticmethod
    def _call_listener(listener: LogListener, entry: LogEntry) -> None:
        # 单个监听器异常不能影响日志主链路
        with contextlib.suppress(Exception):
            listener(entry)

    def get_recent(self, limit: int = 200, since: int = 0) -> list[LogEntry]:
        """按 seq 升序返回日志；since > 0 时只返回比它新的部分（断点续传）"""
        entries = [entry for entry in self.logs.values() if entry.seq > since]
        return entries[-limit:]

    def remove(self, seq: int):
        self.logs.pop(seq, None)


LOG_STORAGE = LogStorage()
