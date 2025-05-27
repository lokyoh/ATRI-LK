class Event:
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
        for listener in self.listeners:
            listener(*args, **kwargs)

    def handle(self):
        """装饰一个函数来响应事件"""

        def wrapper(func):
            self.subscribe(func)
            return func

        return wrapper

class DictEvent:
    """一个字典方式存储监听器的事件"""

    def __init__(self):
        self.listeners = {}

    def subscribe(self, key: str, listener):
        """定义事件监听器"""
        self.listeners[key] = listener

    def unsubscribe(self, key):
        """取消监听器响应事件"""
        del self.listeners[key]

    def notify(self, *args, **kwargs):
        """触发该事件"""
        for key in self.listeners:
            self.listeners[key](*args, **kwargs)

    def handle(self, key: str):
        """装饰一个函数来响应事件"""

        def wrapper(func):
            self.subscribe(func, key)
            return func

        return wrapper
