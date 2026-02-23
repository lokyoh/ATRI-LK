import asyncio
import inspect
from typing import Callable, Dict, List, Any
from enum import IntEnum
from dataclasses import dataclass
from datetime import datetime

from ATRI.exceptions import str_traceback
from ATRI.log import log


class Priority(IntEnum):
    HIGH = 0
    NORMAL = 1
    LOW = 2


@dataclass
class Event:
    """事件对象"""
    type: str
    data: Any
    event_bus: str
    source: str
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


@dataclass
class Subscription:
    """订阅信息"""
    handler: Callable
    priority: Priority = Priority.NORMAL
    once: bool = False  # 是否只执行一次


class AsyncEventBus:
    """异步事件总线"""

    def __init__(self, name: str):
        self._name = name
        self._handlers: Dict[str, List[Subscription]] = {}
        self._middlewares = []

    def subscribe(self, event_type: str, priority: Priority = Priority.NORMAL, once: bool = False):
        """订阅装饰器"""

        def decorator(handler):
            subscription = Subscription(
                handler=handler,
                priority=priority,
                once=once
            )
            if event_type not in self._handlers:
                self._handlers[event_type] = []
            self._handlers[event_type].append(subscription)
            # 按优先级排序
            self._handlers[event_type].sort(key=lambda x: x.priority)
            return handler

        return decorator

    def unsubscribe(self, event_type: str, handler: Callable) -> None:
        """取消订阅"""
        if event_type in self._handlers:
            self._handlers[event_type] = [
                sub for sub in self._handlers[event_type]
                if sub.handler != handler
            ]

    def use(self, middleware):
        """添加中间件"""
        self._middlewares.append(middleware)

    async def _apply_middlewares(self, event: Event) -> Event | None:
        """应用中间件"""
        for middleware in self._middlewares:
            event = await middleware(event)
            if event is None:
                return None
        return event

    async def publish(self, event_type: str, source: str, data: Any = None) -> List[Any]:
        """异步发布事件"""
        event = Event(type=event_type, data=data, event_bus=self._name, source=source)
        # 应用中间件
        event = await self._apply_middlewares(event)
        if event is None:
            return []
        results = []
        if event_type in self._handlers:
            # 复制列表，因为可能在处理过程中修改
            handlers_copy = self._handlers[event_type].copy()
            for subscription in handlers_copy:
                try:
                    sig = inspect.signature(subscription.handler)
                    has_params = len(sig.parameters) > 0
                    if asyncio.iscoroutinefunction(subscription.handler):
                        # 异步函数
                        if has_params:
                            result = await subscription.handler(event)
                        else:
                            result = await subscription.handler()
                    else:
                        # 同步函数
                        if has_params:
                            result = subscription.handler(event)
                        else:
                            result = subscription.handler()
                    results.append(result)
                    # 如果是一次性订阅，处理完后移除
                    if subscription.once:
                        self._handlers[event_type].remove(subscription)
                except Exception as e:
                    log.error(
                        f"{self._name} 来自 {source} 类型为 {event_type} 的事件处理器 {subscription.handler.__name__} 出错: {str_traceback(e)}")
                    results.append(e)
        return results

    def publish_sync(self, event_type: str, data: Any = None, source: str = None) -> List[Any]:
        """同步发布事件"""
        event = Event(type=event_type, data=data, event_bus=self._name, source=source)
        results = []
        if event_type in self._handlers:
            for subscription in self._handlers[event_type]:
                try:
                    result = subscription.handler(event)
                    results.append(result)
                    if subscription.once:
                        self._handlers[event_type].remove(subscription)
                except Exception as e:
                    log.error(
                        f"{self._name} 类型为 {event_type} 的事件处理器 {subscription.handler.__name__} 出错: {str_traceback(e)}")
                    results.append(e)
        return results


async def logging_middleware(event: Event) -> Event:
    """日志中间件"""
    log.debug(f'{event.event_bus} 从 {event.source} 发布事件: {event.type}')
    return event


ATRIEventBus: AsyncEventBus = AsyncEventBus("ATRIEventBus")
"""ATRI系统事件总线"""
ATRIEventBus.use(logging_middleware)


def daily_update(priority: Priority = Priority.NORMAL, once: bool = False):
    """
    注册每日更新事件
    :param priority: 优先级
    :param once: 是否只执行一次
    """

    def decorator(func: Callable) -> Callable:
        ATRIEventBus.subscribe("daily_update", priority=priority, once=once)(func)
        return func

    return decorator


def heartbeat_1m(priority: Priority = Priority.NORMAL, once: bool = False):
    """
    注册1分钟心跳事件
    :param priority: 优先级
    :param once: 是否只执行一次
    """

    def decorator(func: Callable) -> Callable:
        ATRIEventBus.subscribe("heartbeat_1m", priority=priority, once=once)(func)
        return func

    return decorator


def heartbeat_30m(priority: Priority = Priority.NORMAL, once: bool = False):
    """
    注册30分钟心跳事件
    :param priority: 优先级
    :param once: 是否只执行一次
    """

    def decorator(func: Callable) -> Callable:
        ATRIEventBus.subscribe("heartbeat_30m", priority=priority, once=once)(func)
        return func

    return decorator


def shutdown(priority: Priority = Priority.NORMAL, once: bool = False):
    """
    注册关闭事件
    :param priority: 优先级
    :param once: 是否只执行一次
    """

    def decorator(func: Callable) -> Callable:
        ATRIEventBus.subscribe("shutdown", priority=priority, once=once)(func)
        return func

    return decorator
