import asyncio
import contextlib
from collections.abc import Callable

from ATRI.log import log

from ....utils import get_system_status


class StatusBroadcaster:
    """系统状态共享采样器。

    旧实现里每个 WS 连接独立跑一次 psutil（N 个标签页 = N 倍采集），
    现在单任务定时采集一份快照广播给所有订阅者：
    - 首个订阅者出现时启动任务，全部断开后任务自动退出，不后台空转；
    - 订阅回调为同步入队（与 LOG_STORAGE 同一约定），广播路径零 await。
    """

    def __init__(self, interval: float = 5.0):
        self.interval = interval
        self.listeners: set[Callable[[str], None]] = set()
        self.last_payload: str | None = None
        """最近一次快照，新连接接入时立即回放，前端不用等一个采样周期"""
        self._task: asyncio.Task | None = None

    def add_listener(self, callback: Callable[[str], None]) -> None:
        self.listeners.add(callback)
        self._ensure_started()

    def remove_listener(self, callback: Callable[[str], None]) -> None:
        self.listeners.discard(callback)
        # 全部退订后立即停掉采样任务，不后台空转
        if not self.listeners and self._task and not self._task.done():
            self._task.cancel()

    def _ensure_started(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def _run(self) -> None:
        try:
            while self.listeners:
                try:
                    status = await get_system_status()
                    data = status.json()
                    self.last_payload = data
                    for callback in tuple(self.listeners):
                        # 单个订阅者异常不影响广播
                        with contextlib.suppress(Exception):
                            callback(data)
                except Exception as e:
                    log.warning(f"系统状态采集失败:{e}")
                await asyncio.sleep(self.interval)
        finally:
            self._task = None


STATUS_BROADCASTER = StatusBroadcaster()
