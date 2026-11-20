from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from contextlib import AsyncExitStack
from typing import Any

import anyio
import nonebot.message as nb_message
from nonebot.adapters import Bot, Event
from nonebot.exception import IgnoredException, SkippedException
from nonebot.log import logger
from nonebot.typing import T_DependencyCache, T_State
from nonebot.utils import catch, escape_tag, run_coro_with_catch

Handler = Callable[[BaseExceptionGroup], None]
"""`catch` 接受的处理函数"""

ExceptionHandlers = Mapping[
    type[BaseException] | Iterable[type[BaseException]], Handler
]
"""`catch` 接受的异常 → 处理函数映射"""

_SKIP_LOG_EVENT_TYPES = frozenset({"meta_event"})
"""元事件（心跳等）不输出「Running xxxProcessors...」调试日志"""

_PREPROCESSORS_ERROR = (
    "<r><bg #f8bbd0>Error when running EventPreProcessors. "
    "Event ignored!</bg #f8bbd0></r>"
)
_POSTPROCESSORS_ERROR = (
    "<r><bg #f8bbd0>Error when running EventPostProcessors</bg #f8bbd0></r>"
)


async def _run_processors(
    processors: Iterable[Any],
    handlers: ExceptionHandlers,
    title: str,
    *,
    bot: Bot,
    event: Event,
    state: T_State,
    stack: AsyncExitStack | None,
    dependency_cache: T_DependencyCache | None,
    show_log: bool,
) -> None:
    """并发运行一组事件处理器，异常交给 `handlers` 统一处理。"""
    if show_log and event.get_type() not in _SKIP_LOG_EVENT_TYPES:
        logger.debug(f"Running {title}...")

    with catch(handlers):
        async with anyio.create_task_group() as tg:
            for proc in processors:
                tg.start_soon(
                    run_coro_with_catch,
                    proc(
                        bot=bot,
                        event=event,
                        state=state,
                        stack=stack,
                        dependency_cache=dependency_cache,
                    ),
                    (SkippedException,),
                )


async def apply_event_preprocessors(
    bot: Bot,
    event: Event,
    state: T_State,
    stack: AsyncExitStack | None = None,
    dependency_cache: T_DependencyCache | None = None,
    show_log: bool = True,
) -> bool:
    """运行事件预处理，返回是否继续处理事件。

    与上游的差别只有：元事件（心跳等）不输出调试日志。
    """
    if not nb_message._event_preprocessors:
        return True

    await _run_processors(
        nb_message._event_preprocessors,
        {
            IgnoredException: nb_message._handle_ignored_exception(
                f"Event {escape_tag(event.get_event_name())} is <b>ignored</b>"
            ),
            Exception: nb_message._handle_exception(_PREPROCESSORS_ERROR),
        },
        "PreProcessors",
        bot=bot,
        event=event,
        state=state,
        stack=stack,
        dependency_cache=dependency_cache,
        show_log=show_log,
    )
    return True


async def apply_event_postprocessors(
    bot: Bot,
    event: Event,
    state: T_State,
    stack: AsyncExitStack | None = None,
    dependency_cache: T_DependencyCache | None = None,
    show_log: bool = True,
) -> None:
    """运行事件后处理；元事件同样不输出调试日志。"""
    if not nb_message._event_postprocessors:
        return

    await _run_processors(
        nb_message._event_postprocessors,
        {Exception: nb_message._handle_exception(_POSTPROCESSORS_ERROR)},
        "PostProcessors",
        bot=bot,
        event=event,
        state=state,
        stack=stack,
        dependency_cache=dependency_cache,
        show_log=show_log,
    )


def install() -> None:
    """接管 NoneBot 的事件处理器调度；重复调用无副作用。"""
    nb_message._apply_event_preprocessors = apply_event_preprocessors
    nb_message._apply_event_postprocessors = apply_event_postprocessors


install()
