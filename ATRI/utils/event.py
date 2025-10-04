from nonebot.exception import FinishedException

from ATRI.log import log
from ATRI.exceptions import str_traceback, EventRuntimeError


class BaseEvent:
    """
    基础事件体。
    """

    def __init__(self, event_name):
        self.event_name = event_name
        self.error = False
        self.error_listeners = []
        self.result = {}

    def add_result(self, result, priority: int = 10):
        """
        为事件体添加结果。
        :param result: 需要添加的结果
        :param priority: 优先级
        """
        if priority not in self.result:
            self.result[priority] = []
        self.result[priority].append(result)

    def get_result(self):
        """
        获取结果。
        :return: 结果列表。
        """
        return [item for _, value in sorted(self.result.items()) for item in value]


class BaseListener:
    """
    基础监听器。
    """

    def __init__(self, listener_name):
        self.listener_name = listener_name

    def notify(self, event: BaseEvent):
        """
        激活监听器。
        :param event: 事件体。
        """
        pass


class InnerListener(BaseListener):
    """
    内置监听器,将方法转为监听器。
    """

    def __init__(self, func):
        super().__init__(func.__name__)
        self.func = func
        if hasattr(self.func, '__code__'):
            code = self.func.__code__
            self.param = code.co_argcount
        elif hasattr(self.func, '__func__'):
            code = self.func.__func__.__code__
            self.param = code.co_argcount
        else:
            raise AttributeError

    def notify(self, event: BaseEvent):
        if self.param == 0:
            self.func()
        else:
            self.func(event)


class BaseEvents:
    """
    一个基础事件。
    """

    def __init__(self, stop_when_error=False):
        self.listeners = {}
        self.stop_when_error = stop_when_error

    def subscribe(self, listener: BaseListener, priority: int = 10):
        """
        添加事件监听器。
        :param listener: 监听器。
        :param priority: 触发优先级
        """
        if priority not in self.listeners:
            self.listeners[priority] = []
        self.listeners[priority].append(listener)
        self.listeners = dict(sorted(self.listeners.items()))

    def unsubscribe(self, listener_name: str):
        """
        取消监听器响应事件。
        :param listener_name: 事件名
        """
        for listeners in self.listeners.values():
            for listener in listeners:
                if listener.listener_name == listener_name:
                    listeners.remove(listener)
                    return

    def notify(self, event: BaseEvent) -> BaseEvent:
        """
        触发该事件。
        :param event: 事件体
        """
        exceptions = {}
        for values in self.listeners.values():
            break_sign = False
            for listener in values:
                try:
                    listener.notify(event)
                except Exception as e:
                    event.error = True
                    event.error_listeners.append(listener.listener_name)
                    tb = str_traceback(e)
                    exceptions[listener.listener_name] = tb
                    log.error(tb)
                    if self.stop_when_error:
                        break_sign = True
                        break
            if break_sign:
                break
        if event.error:
            formatted_str = "\n".join([f"\n{key}:{value}" for key, value in exceptions.items()])
            key_str = ",".join([key for key in exceptions])
            raise EventRuntimeError(f"事件{event.event_name}在执行{key_str}时出现错误", formatted_str)
        return event

    def handle(self, priority: int = 10):
        """
        装饰一个函数来响应事件。
        """

        def wrapper(func):
            if func.__name__ == '_':
                raise ValueError('请不要使用`_`作为函数名')
            self.subscribe(InnerListener(func), priority)
            return func

        return wrapper


class AsyncInnerListener(BaseListener):
    """
    内置监听器,将方法转为监听器。
    """

    def __init__(self, func):
        super().__init__(func.__name__)
        self.func = func
        if hasattr(self.func, '__code__'):
            code = self.func.__code__
            self.param = code.co_argcount
        elif hasattr(self.func, '__func__'):
            code = self.func.__func__.__code__
            self.param = code.co_argcount
        else:
            raise AttributeError

    async def notify(self, event: BaseEvent):
        if self.param == 0:
            await self.func()
        else:
            await self.func(event)


class AsyncBaseEvents(BaseEvents):
    """
    一个基础事件。
    """

    def __init__(self, stop_when_error=False):
        super().__init__(stop_when_error)

    async def notify(self, event: BaseEvent) -> BaseEvent:
        """
        触发该事件。
        :param event: 事件体
        """
        exceptions = {}
        for values in self.listeners.values():
            break_sign = False
            for listener in values:
                try:
                    await listener.notify(event)
                except FinishedException:
                    pass
                except Exception as e:
                    event.error = True
                    event.error_listeners.append(listener.listener_name)
                    tb = str_traceback(e)
                    exceptions[listener.listener_name] = tb
                    log.error(tb)
                    if self.stop_when_error:
                        break_sign = True
                        break
            if break_sign:
                break
        if event.error:
            formatted_str = "\n".join([f"\n{key}:{value}" for key, value in exceptions.items()])
            key_str = ",".join([key for key in exceptions])
            raise EventRuntimeError(f"事件{event.event_name}在执行{key_str}时出现错误", formatted_str)
        return event

    def handle(self, priority: int = 10):
        """
        装饰一个函数来响应事件。
        """

        def wrapper(func):
            if func.__name__ == '_':
                raise ValueError('请不要使用`_`作为函数名')
            self.subscribe(AsyncInnerListener(func), priority)
            return func

        return wrapper
