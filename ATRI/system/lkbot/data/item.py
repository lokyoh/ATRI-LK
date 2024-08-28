import copy
import json
from enum import Enum
from typing import Dict, Any

from ATRI.log import log


class ItemType(Enum):
    """物品种类"""
    TOOL = '工具'
    PROP = '道具'
    SEED = '种子'
    VEGETABLE = '蔬菜'
    FRUIT = '水果'
    FLOWER = '花'
    COIN = '货币'
    OTHER = '其他'
    ERROR = "未知物品"


class Item:
    """定义物品及其基本数据，注意默认方法无物品消耗，请自行添加"""

    def __init__(self, item_name, item_type: ItemType = ItemType.OTHER, item_info='', item_price=0, using_func=None):
        """
        name 物品名称，要求不为空
        """
        self._name = item_name
        self._type = item_type
        self._info = item_info
        self._price = item_price
        self._using_func = using_func

    def use_item(self, user_id: str):
        """在背包使用指定物品"""
        if self._using_func is None:
            return None
        return self._using_func(user_id)

    def get_item_name(self) -> str:
        """获取物品名"""
        return self._name

    def get_item_info(self) -> str:
        """获取物品介绍"""
        if self._info == '':
            return '暂无介绍'
        return self._info

    def get_item_price_dis(self) -> str:
        """获取物品回收价格的字符串"""
        if self._price <= 0:
            return '无价值'
        return f'{self._price}ATRI币'

    def get_item_price(self) -> int:
        """获取物品回收价格的数值"""
        return self._price

    def item_can_use(self) -> bool:
        """获取物品是否能在背包中使用"""
        if self._using_func is None:
            return False
        return True

    def get_item_type(self) -> ItemType:
        """获取物品的类型的ItemType对象"""
        return self._type


class ItemRegister:
    """物品注册器"""

    def __init__(self):
        self._item_list = []
        self._item_dic = {}
        self._item_type_dic = {}
        for _type in ItemType:
            self._item_type_dic[_type.value] = []

    def register(self, item: Item):
        """将定义的物品注册进物品表中"""
        item_name = item.get_item_name()
        if item_name is None or item_name == '':
            log.error(f"物品注册失败:{item_name} 不是有效物品名称")
            return
        if item_name in self._item_list:
            log.error(f"物品注册失败:物品名称 {item_name} 重复使用")
            return
        self._item_list.append(item_name)
        self._item_dic[item_name] = item
        self._item_type_dic[item.get_item_type().value].append(item_name)
        return self

    def get_item_by_index(self, index) -> Item | None:
        """获通过索引值获取Item对象"""
        if 0 <= index < len(self._item_list):
            return self._item_dic[self._item_list[index]]
        return None

    def get_item_by_name(self, item_name) -> Item | None:
        """通过名称获取Item对象"""
        if item_name in self._item_dic:
            return self._item_dic[item_name]
        return None

    def get_item_index(self, item_name) -> int | None:
        """获取物品在注册器中的索引值"""
        if item_name in self._item_list:
            return self._item_list.index(item_name)
        return None

    def get_item_list_by_type(self, item_type: ItemType) -> list[str]:
        """获取指定类型的物品列表"""
        return self._item_type_dic[item_type.value]

    def get_item_list(self) -> list[str]:
        """获取物品列表"""
        return self._item_list

    def has_item(self, item_name):
        """获取是否注册指定物品"""
        if item_name in self._item_list:
            return True
        return False

    def items_clear(self):
        """清空物品注册表"""
        self._item_list = []
        self._item_dic = {}
        self._item_type_dic = {}
        for _type in ItemType:
            self._item_type_dic[_type.value] = []

    def get_reg_item_type(self, item_name: str):
        """获取物品种类，没有物品时返回未知物品"""
        for _type in ItemType:
            if item_name in self._item_type_dic[_type.value]:
                return _type
        return ItemType.ERROR


items = ItemRegister()


class ItemMeta:
    """物品数据"""

    def __init__(self, item_meta: dict[str: Any]):
        item_meta: dict
        self.num: int = item_meta.pop("num", 0)
        self.extra = copy.deepcopy(item_meta)

    def meta_to_dict(self) -> dict[str: int]:
        """转换成字典"""
        meta = copy.deepcopy(self.extra)
        meta["num"] = self.num
        return meta


class ItemStack:
    """物品堆：物品的个性化，带有特殊信息。不要轻易构造该类，除非确保item_type符合item"""

    def __init__(self, item_name: str, item_type: ItemType, item_meta: ItemMeta):
        self._name = item_name
        self._type = item_type
        self.meta = item_meta

    def get_name(self):
        """获取物品名"""
        return self._name

    def get_type(self):
        """获取物品种类"""
        return self._type


class BackPack:
    """背包：里面是通过ItemType分类的物品名称与ItemMeta的字典。以ItemStack形式获取信息。
    bp_dict结构:
        {
            "item_name": {"key": value}
        }"""

    def __init__(self, bp_dict: dict):
        global items
        self._backpack: Dict[ItemType: Dict[str: ItemMeta]] = dict()
        self._wrong_item = {}
        for _type in ItemType:
            self._backpack[_type] = dict()
        for _name in bp_dict.keys():
            _type = items.get_reg_item_type(_name)
            _meta = bp_dict[_name]
            self._backpack[_type][_name] = ItemMeta(_meta)

    def bp_has_item(self, item_name: str) -> bool:
        """背包中有指定物品"""
        for _type in ItemType:
            if item_name in self._backpack[_type]:
                return True
        return False

    def get_item_stack(self, item_name: str) -> ItemStack:
        """获取背包中指定物品的物品堆，没有则返回新物品堆"""
        for _type in ItemType:
            if item_name in self._backpack[_type]:
                return ItemStack(item_name, _type, self._backpack[_type][item_name])
        return ItemStack(item_name, items.get_reg_item_type(item_name), ItemMeta({}))

    def set_item_with_stack(self, item_stack: ItemStack):
        """通过物品堆设置物品"""
        _name = item_stack.get_name()
        _type = item_stack.get_type()
        if item_stack.meta.num == 0:
            self.remove_item(_name)
        else:
            self._backpack[_type][_name] = item_stack.meta

    def set_item_with_meta(self, item_name: str, item_meta: dict[str: Any]):
        """通过物品数据设置物品"""
        _type = items.get_reg_item_type(item_name)
        if item_meta.get("num", 0) == 0:
            self.remove_item(item_name)
        else:
            self._backpack[_type][item_name] = ItemMeta(item_meta)

    def set_item(self, item_name: str, num: int):
        """设置指定数量的无其余数据的物品"""
        self.set_item_with_meta(item_name, {"num": num})

    def remove_item(self, item_name: str) -> bool:
        """移除背包中的指定物品"""
        for _type in ItemType:
            if item_name in self._backpack[_type].keys():
                del self._backpack[_type][item_name]
                return True
        return False

    def bp_to_dict(self) -> dict[str: str]:
        """将背包转换成字典"""
        bp_dict = {}
        for _type in ItemType:
            for _name in self._backpack[_type].keys():
                bp_dict[_name] = self._backpack[_type][_name].meta_to_dict()
        for wrong_item in self._wrong_item:
            bp_dict[wrong_item] = self._wrong_item[wrong_item]
        return bp_dict

    def bp_to_str(self, ensure_ascii: bool = False) -> str:
        """将背包转换成字符串"""
        return json.dumps(self.bp_to_dict(), ensure_ascii=ensure_ascii)

    def get_item_list(self) -> list[ItemStack]:
        """获取背包中的物品列表"""
        item_list = []
        for _type in ItemType:
            for _name in self._backpack[_type].keys():
                item_list.append(ItemStack(_name, _type, self._backpack[_type][_name]))
        return item_list

    def get_item_list_by_type(self, item_type: ItemType) -> list[ItemStack] | None:
        """获取背包中指定类型的物品列表"""
        if item_type in self._backpack.keys():
            item_list = []
            for _name in self._backpack[item_type].keys():
                item_list.append(ItemStack(_name, item_type, self._backpack[item_type][_name]))
            return item_list
        return None
