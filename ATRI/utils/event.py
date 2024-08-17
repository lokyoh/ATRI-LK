class Event:
    """一个基础事件"""

    def __init__(self):
        self.listeners = []

    def subscribe(self, listener):
        """定义事件响应器"""
        self.listeners.append(listener)

    def unsubscribe(self, listener):
        """取消响应器响应事件"""
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
