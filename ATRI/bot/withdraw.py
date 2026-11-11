import asyncio
import contextlib
import heapq
import time
from contextvars import ContextVar
from typing import Any

from nonebot.adapters import Bot, Event

from ATRI import driver
from ATRI.log import log

STATE_KEY = "atri_withdraw_delay"

withdraw_delay: ContextVar[float] = ContextVar("atri_withdraw_delay", default=0.0)

_INSTALLED_FLAG = "_atri_withdraw_installed"


def set_delay(delay: float) -> None:
    """设置当前上下文的自动撤回延迟（供 handler 之外显式发送使用）。"""
    withdraw_delay.set(max(0.0, float(delay or 0.0)))


def current_delay() -> float:
    return withdraw_delay.get()


def _state_delay() -> float | None:
    """读取当前 matcher state 里的延迟；没有 matcher 时返回 ``None``。"""
    from nonebot.matcher import current_matcher

    try:
        matcher = current_matcher.get()
    except LookupError:
        return None
    value = getattr(matcher, "state", {}).get(STATE_KEY)
    return None if value is None else max(0.0, float(value))


def resolve_delay() -> float:
    """发送时实际使用的延迟：matcher state 优先，其次上下文变量。"""
    delay = _state_delay()
    return current_delay() if delay is None else delay


def extract_message_id(result: Any) -> str | None:
    """从适配器 ``send`` 的返回值里取消息 id。

    OneBot v11 返回 ``{"message_id": 123}``；QQ 官方返回带 ``id`` 的模型。
    """
    if isinstance(result, dict):
        for key in ("message_id", "id"):
            value = result.get(key)
            if value not in (None, ""):
                return str(value)
        return None
    for attr in ("message_id", "id"):
        value = getattr(result, attr, None)
        if value not in (None, ""):
            return str(value)
    return None


async def withdraw_message(bot: Bot, event: Event, message_id: str) -> bool:
    """撤回一条消息。

    适配器不支持或撤回失败时只记 debug 日志并返回 ``False``，不向上抛异常。
    """
    try:
        if hasattr(bot, "delete_msg"):  # OneBot v11
            await bot.delete_msg(message_id=int(message_id))  # type: ignore[attr-defined]
            return True
        channel_id = getattr(event, "channel_id", "")
        group_openid = getattr(event, "group_openid", "")
        author = getattr(event, "author", None)
        if channel_id and hasattr(bot, "delete_message"):  # QQ 频道
            await bot.delete_message(  # type: ignore[attr-defined]
                channel_id=channel_id, message_id=message_id
            )
            return True
        if group_openid and hasattr(bot, "delete_group_message"):  # QQ 群
            await bot.delete_group_message(  # type: ignore[attr-defined]
                group_openid=group_openid, message_id=message_id
            )
            return True
        if author is not None and hasattr(bot, "delete_c2c_message"):  # QQ 私聊
            await bot.delete_c2c_message(  # type: ignore[attr-defined]
                openid=getattr(author, "id", ""), message_id=message_id
            )
            return True
    except Exception as e:
        log.debug(f"撤回消息失败：{e}")
        return False
    log.debug(f"当前适配器不支持撤回，跳过：bot={type(bot).__name__}")
    return False


class WithdrawManager:
    """最小堆 + 后台任务，按到期时间撤回消息。"""

    def __init__(self) -> None:
        self._heap: list[tuple[float, int, Bot, Event, str]] = []
        self._seq = 0
        self._task: asyncio.Task | None = None
        self._wakeup = asyncio.Event()

    @property
    def pending(self) -> int:
        return len(self._heap)

    def schedule(
        self, bot: Bot, event: Event, message_id: str | None, delay: float
    ) -> None:
        if delay <= 0 or not message_id:
            return
        self._seq += 1
        heapq.heappush(
            self._heap,
            (time.monotonic() + delay, self._seq, bot, event, str(message_id)),
        )
        self._ensure_task()

    def _ensure_task(self) -> None:
        if self._task is not None and not self._task.done():
            return
        try:
            self._task = asyncio.create_task(self._run())
        except RuntimeError:
            # 没有运行中的事件循环（同步调用 / 关闭阶段），直接放弃这批任务
            self._heap.clear()
            self._task = None

    async def _run(self) -> None:
        try:
            while self._heap:
                due = self._heap[0][0] - time.monotonic()
                if due > 0:
                    self._wakeup.clear()
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._wakeup.wait(), timeout=due)
                    continue
                _, _, bot, event, message_id = heapq.heappop(self._heap)
                await withdraw_message(bot, event, message_id)
        finally:
            self._task = None

    async def run_due(self, *, current: float | None = None) -> int:
        """立即执行已到期的任务（测试或关闭前兜底），返回执行条数。"""
        now = time.monotonic() if current is None else current
        done = 0
        while self._heap and self._heap[0][0] <= now:
            _, _, bot, event, message_id = heapq.heappop(self._heap)
            await withdraw_message(bot, event, message_id)
            done += 1
        return done

    async def shutdown(self) -> None:
        self._heap.clear()
        task, self._task = self._task, None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.debug(f"撤回后台任务退出异常：{e}")


withdraw_manager = WithdrawManager()


def install_bot(bot: Bot) -> None:
    """包装单个 bot 的 ``send``，按当前延迟登记撤回。"""
    if getattr(bot, _INSTALLED_FLAG, False):
        return
    original = bot.send

    async def send(event: Event, message: Any, **kwargs: Any) -> Any:
        result = await original(event, message, **kwargs)
        delay = resolve_delay()
        if delay > 0:
            message_id = extract_message_id(result)
            if message_id:
                withdraw_manager.schedule(bot, event, message_id, delay)
        return result

    bot.send = send  # type: ignore[method-assign]
    setattr(bot, _INSTALLED_FLAG, True)


_installed = False


def install_withdraw() -> None:
    """注册 ``on_bot_connect`` / ``on_shutdown`` 钩子（重复调用无副作用）。"""
    global _installed
    if _installed:
        return
    _installed = True
    driver().on_bot_connect(install_bot)
    driver().on_shutdown(withdraw_manager.shutdown)
