import nonebot.message as nb_message
from nonebot.message import *  # noqa: F403
from nonebot.message import (
    _event_postprocessors,
    _event_preprocessors,
    _handle_exception,
    _handle_ignored_exception,
)


async def new_apply_event_preprocessors(
    bot: "Bot",
    event: "Event",
    state: T_State,
    stack: Optional[AsyncExitStack] = None,
    dependency_cache: Optional[T_DependencyCache] = None,
    show_log: bool = True,
) -> bool:
    """运行事件预处理。

    参数:
        bot: Bot 对象
        event: Event 对象
        state: 会话状态
        stack: 异步上下文栈
        dependency_cache: 依赖缓存
        show_log: 是否显示日志

    返回:
        是否继续处理事件
    """
    if not _event_preprocessors:
        return True

    if show_log and event.get_type() != "meta_event":
        logger.debug("Running PreProcessors...")

    with catch(
        {
            IgnoredException: _handle_ignored_exception(
                f"Event {escape_tag(event.get_event_name())} is <b>ignored</b>"
            ),
            Exception: _handle_exception(
                "<r><bg #f8bbd0>Error when running EventPreProcessors. "
                "Event ignored!</bg #f8bbd0></r>"
            ),
        }
    ):
        async with anyio.create_task_group() as tg:
            for proc in _event_preprocessors:
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

        return True

    return False


async def new_apply_event_postprocessors(
    bot: "Bot",
    event: "Event",
    state: T_State,
    stack: Optional[AsyncExitStack] = None,
    dependency_cache: Optional[T_DependencyCache] = None,
    show_log: bool = True,
) -> None:
    """运行事件后处理。

    参数:
        bot: Bot 对象
        event: Event 对象
        state: 会话状态
        stack: 异步上下文栈
        dependency_cache: 依赖缓存
        show_log: 是否显示日志
    """
    if not _event_postprocessors:
        return

    if show_log and event.get_type() != "meta_event":
        logger.debug("Running PostProcessors...")

    with catch(
        {
            Exception: _handle_exception(
                "<r><bg #f8bbd0>Error when running EventPostProcessors</bg #f8bbd0></r>"
            )
        }
    ):
        async with anyio.create_task_group() as tg:
            for proc in _event_postprocessors:
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


nb_message._apply_event_preprocessors = new_apply_event_preprocessors
nb_message._apply_event_postprocessors = new_apply_event_postprocessors
