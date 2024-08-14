import time
from collections import deque


class RateLimiter:
    """频率限制器"""

    def __init__(self, max_calls, period):
        """
        初始化限制器
        :param max_calls: 最大允许调用次数
        :param period: 时间窗口（秒）
        """
        self.max_calls = max_calls
        self.period = period
        self.calls = deque()

    def is_allowed(self):
        """
        检查是否允许调用
        :return: 达到限制时返回False，没达到限制时返回True
        """
        current_time = time.time()

        # 移除窗口外的旧调用记录
        while self.calls and self.calls[0] < current_time - self.period:
            self.calls.popleft()

        if len(self.calls) < self.max_calls:
            self.calls.append(current_time)
            return True
        else:
            return False


class LimitedQueue:
    """队列限制器"""

    def __init__(self, max_size):
        """
        初始化存储队列
        :param max_size: 队列的最大大小
        """
        self.queue = deque(maxlen=max_size)
        self.max_size = max_size

    def add(self, item):
        """
        向队列中添加新数据
        :param item: 要添加的数据
        :return: 如果队列超过最大大小，返回最旧的数据；否则返回None
        """
        if len(self.queue) == self.max_size:
            oldest = self.queue.popleft()
            self.queue.append(item)
            return oldest
        else:
            self.queue.append(item)
            return None

    def get_data(self):
        """
        返回队列中的数据，数据类型为list
        :return: 目前队列中的数据
        """
        return list(self.queue)

    def __repr__(self):
        return f"LimitedQueue({list(self.queue)})"
