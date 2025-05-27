import importlib
import inspect

from ATRI.exceptions import BaseBotException


class ConditionNotMet(BaseBotException):
    prompt = "使用条件不符"


class ItemUsingFunc:
    """物品的使用功能的参数"""

    def __init__(self, func_name, args):
        self.func_name = func_name
        self.args = args


class ItemFuncs:
    """物品的使用功能参数集，包含一个效果器参数列表与一个检查器参数列表"""

    def __init__(self, checks: list[ItemUsingFunc] = None, funcs: list[ItemUsingFunc] = None):
        if checks is None:
            checks = []
        if funcs is None:
            funcs = []
        self.checks = checks
        self.funcs = funcs


class ItemFuncRegister:
    """物品功能注册器"""

    def __init__(self):
        self._func_list = {}
        self._check_list = {}

    def add_func(self, func):
        """添加效果器"""
        self._func_list[func.__name__] = func

    def add_check(self, check):
        """添加检查器"""
        self._check_list[check.__name__] = check

    def exec_func(self, func_data: ItemUsingFunc, user_id):
        """执行效果器"""
        return self._func_list[func_data.func_name](user_id, func_data.args)

    def exec_check(self, func_data: ItemUsingFunc, user_id):
        """执行检查器"""
        return self._check_list[func_data.func_name](user_id, func_data.args)

    def check_func(self, name: str, model: str = 'func'):
        """检查指定功能器是否存在"""
        f_list = self._func_list if model == 'func' else self._check_list
        if name not in f_list.keys():
            raise RuntimeError(f'找不到已注册的功能{name}')

    def check_num(self):
        """检查器数量"""
        return len(self._check_list)

    def func_num(self):
        """效果器数量"""
        return len(self._func_list)


item_funcs = ItemFuncRegister()
"""所有的物品功能"""


def register_core_func():
    """core的物品注册方法"""
    module = importlib.import_module("ATRI.system.lkbot.data.core_item_func_checks")

    for _, obj in inspect.getmembers(module, inspect.isfunction):
        item_funcs.add_check(obj)

    module = importlib.import_module("ATRI.system.lkbot.data.core_item_funcs")

    for _, obj in inspect.getmembers(module, inspect.isfunction):
        item_funcs.add_func(obj)
