from ATRI.log import log
from ATRI.exceptions import str_traceback, BotRuntimeError

class BaseEvent:
    """一个基础事件"""

    def __init__(self):
        self.listeners = []

    def subscribe(self, listener):
        """定义事件监听器"""
        self.listeners.append(listener)

    def unsubscribe(self, listener):
        """取消监听器响应事件"""
        self.listeners.remove(listener)

    def notify(self, *args, **kwargs):
        """触发该事件"""
        exception = False
        for listener in self.listeners:
            try:
                listener(*args, **kwargs)
            except Exception as e:
                exception = True
                log.warning(str_traceback(e))
        if exception:
            raise BotRuntimeError("事件触发过程中出现错误，请检查后台代码输出")

    def handle(self):
        """装饰一个函数来响应事件"""

        def wrapper(func):
            self.subscribe(func)
            return func

        return wrapper


class DictEvent:
    """一个字典方式存储监听器的事件"""

    def __init__(self, name):
        self.listeners = {}
        self.name = name

    def subscribe(self, key: str, listener):
        """定义事件监听器"""
        if not key:
            raise ValueError(f"`{self.name}`未命名监听器")
        if key in self.listeners:
            log.info(f"`{self.name}`事件中监听器`{key}`已经存在并覆盖")
        self.listeners[key] = listener

    def unsubscribe(self, key):
        """取消监听器响应事件"""
        del self.listeners[key]

    def notify(self, *args, **kwargs):
        """触发该事件"""
        exceptions = {}
        for key in self.listeners:
            try:
                self.listeners[key](*args, **kwargs)
            except Exception as e:
                str_tb = str_traceback(e)
                exceptions[key] = str_tb
                log.warning(str_tb)
        if exceptions:
            formatted_str = "".join([f"\n{key}:{value}" for key, value in exceptions.items()])
            raise BotRuntimeError(f"以下事件出现错误:{formatted_str}")

    def handle(self, key: str):
        """装饰一个函数来响应事件"""

        def wrapper(func):
            self.subscribe(key, func)
            return func

        return wrapper
