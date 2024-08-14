from enum import Enum
from pydantic import BaseModel

from ATRI.log import log


class ItemType(Enum):
    OTHER = '其他'
    PROP = '道具'
    COIN = '货币'


class Item:
    def __init__(self, item_name, item_type: ItemType = ItemType.OTHER, item_info='', item_price=0, using_func=None):
        """
        name 物品名称，要求不为空
        """
        self._name = item_name
        self._type = item_type
        self._info = item_info
        self._price = item_price
        self._using_func = using_func

    def use_item(self, *args, **kwargs):
        if self._using_func is None:
            return None
        return self._using_func(*args, **kwargs)

    def get_item_name(self) -> str:
        return self._name

    def get_item_info(self) -> str:
        if self._info == '':
            return '暂无介绍'
        return self._info

    def get_item_price_dis(self) -> str:
        if self._price <= 0:
            return '无价值'
        return f'{self._price}ATRI币'

    def get_item_price(self) -> int:
        return self._price

    def item_can_use(self) -> bool:
        if self._using_func is None:
            return False
        return True

    def get_item_type(self) -> str:
        return self._type.value


class ItemRegister:
    def __init__(self):
        self._item_list = []
        self._item_dic = {}
        self._item_type_dic = {}
        for _type in ItemType:
            self._item_type_dic[_type.value] = []

    def register(self, item: Item):
        item_name = item.get_item_name()
        if item_name is None or item_name == '':
            log.error(f"物品注册失败:{item_name} 不是有效物品名称")
            return
        if item_name in self._item_list:
            log.error(f"物品注册失败:物品名称 {item_name} 重复使用")
            return
        self._item_list.append(item_name)
        self._item_dic[item_name] = item
        self._item_type_dic[item.get_item_type()].append(item_name)

    def get_item_by_index(self, index) -> Item | None:
        if 0 <= index < len(self._item_list):
            return self._item_dic[self._item_list[index]]
        return None

    def get_item_by_name(self, item_name) -> Item | None:
        if item_name in self._item_dic:
            return self._item_dic[item_name]
        return None

    def get_item_index(self, item_name) -> int | None:
        if item_name in self._item_list:
            return self._item_list.index(item_name)
        return None

    def get_item_list_by_type(self, item_type: ItemType) -> list:
        return self._item_type_dic[item_type.value]

    def get_item_list(self):
        return self._item_list

    def has_item(self, item_name):
        if item_name in self._item_list:
            return True
        return False

    def items_clear(self):
        self._item_list = []
        self._item_dic = {}
        self._item_type_dic = {}
        for _type in ItemType:
            self._item_type_dic[_type.value] = []


items = ItemRegister()


class ItemStack:
    def __init__(self):
        pass
