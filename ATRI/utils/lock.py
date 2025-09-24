from threading import Lock
from typing import Dict


class SingleLock:
    """数据锁，保证数据最多只有一个"""

    def __init__(self):
        self._lock = Lock()

    def run(self, func: ()):
        """
        使用方式:
        @your_lock.run
        def your_func(args):
            ...
        your_func(args)
        或者:
        your_lock.run(your_func)(args)
        """

        def wrapper(*args, **kwargs):
            self._lock.acquire()
            try:
                r = func(*args, **kwargs)
            except Exception:
                self._lock.release()
                raise
            self._lock.release()
            return r

        return wrapper


class GroupLock:
    """数据锁组，数据锁的组合版"""

    def __init__(self):
        self._lock: Dict[str: Lock] = {}

    def add_lock(self, key: str):
        """添加锁的键值"""
        self._lock[key] = Lock()

    def run(self, key: str, func: ()):
        """
        用法:
        your_locks.run(key, your_func)
        """
        if key in self._lock:
            self._lock[key].acquire()
            try:
                r = func()
            except Exception:
                self._lock[key].release()
                raise
            self._lock[key].release()
            return r
        else:
            raise ValueError(f"非法关键字{key}")

    def __getitem__(self, key: str):
        if key not in self._lock:
            self.add_lock(key)
        return self._lock[key]

    def lock(self, key: str):
        """
        用法:
        @your_locks.lock(key)
        """

        def handle_wrapper(func):
            def wrapper(*args, **kwargs):
                if key in self._lock:
                    self._lock[key].acquire()
                    try:
                        r = func(*args, **kwargs)
                    except Exception:
                        self._lock[key].release()
                        raise
                    self._lock[key].release()
                    return r
                else:
                    raise ValueError(f"非法关键字{key}")

            return wrapper

        return handle_wrapper
