import copy
from enum import Enum
from typing import Dict, Any

from ATRI.log import log


class ItemType(Enum):
    """物品种类"""
    TOOL = '工具'
    PROP = '道具'
    SEED = '种子'
    CROP = '作物'
    COIN = '货币'
    OTHER = '其他'


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

    def get_item_type_name(self) -> ItemType:
        return self._type


class ItemRegister:
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
        self._item_type_dic[item.get_item_type()].append(item_name)
        return self

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

    def get_item_list_by_type(self, item_type: ItemType) -> list[str]:
        return self._item_type_dic[item_type.value]

    def get_item_list(self) -> list[str]:
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


class ItemMeta:
    """物品数据"""

    def __init__(self, item_meta: dict[str: Any]):
        item_meta: dict
        self.num: int = item_meta.pop("num", 0)
        self.extra = copy.deepcopy(item_meta)

    def meta_to_dict(self) -> dict[str: int]:
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
        return self._name

    def get_type(self):
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
            _item = items.get_item_by_name(_name)
            if not _item:
                self._wrong_item[_name] = bp_dict[_name]
                continue
            _type = _item.get_item_type_name()
            _meta = bp_dict[_name]
            self._backpack[_type][_name] = ItemMeta(_meta)

    def bp_has_item(self, item_name: str) -> bool:
        for _type in ItemType:
            if item_name in self._backpack[_type]:
                return True
        return False

    def get_item_stack(self, item_name: str) -> ItemStack:
        for _type in ItemType:
            if item_name in self._backpack[_type]:
                return ItemStack(item_name, _type, self._backpack[_type][item_name])
        return ItemStack(item_name, items.get_item_by_name(item_name).get_item_type_name(), ItemMeta({}))

    def set_item_with_stack(self, item_stack: ItemStack):
        _name = item_stack.get_name()
        _type = item_stack.get_type()
        if item_stack.meta.num == 0:
            self.remove_item(_name)
        self._backpack[_type][_name] = item_stack.meta

    def set_item_with_meta(self, item_name: str, item_meta: dict[str: Any]):
        _type = items.get_item_by_name(item_name).get_item_type_name()
        if item_meta.get("num", 0) == 0:
            self.remove_item(item_name)
        self._backpack[_type][item_name] = ItemMeta(item_meta)

    def set_item(self, item_name: str, num: int):
        self.set_item_with_meta(item_name, {"num": num})

    def remove_item(self, item_name: str) -> bool:
        for _type in ItemType:
            if item_name in self._backpack[_type].keys():
                del self._backpack[_type][item_name]
                return True
        return False

    def bp_to_dict(self) -> dict[str: str]:
        bp_dict = {}
        for _type in ItemType:
            for _name in self._backpack[_type].keys():
                bp_dict[_name] = self._backpack[_type][_name].meta_to_dict()
        for wrong_item in self._wrong_item:
            bp_dict[wrong_item] = self._wrong_item[wrong_item]
        return bp_dict

    def get_item_list(self) -> list[ItemStack]:
        item_list = []
        for _type in ItemType:
            for _name in self._backpack[_type].keys():
                item_list.append(ItemStack(_name, _type, self._backpack[_type][_name]))
        return item_list

    def get_item_list_by_type(self, item_type: ItemType) -> list[ItemStack] | None:
        if item_type in self._backpack.keys():
            item_list = []
            for _name in self._backpack[item_type].keys():
                item_list.append(ItemStack(_name, item_type, self._backpack[item_type][_name]))
            return item_list
        return None
