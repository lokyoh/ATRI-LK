import json
from enum import Enum
from typing import Dict, Any
from typing import TypeVar

from ATRI.log import log
from ATRI.message import MessageBuilder

from .item_func import ConditionNotMet, ItemFuncs, item_funcs


class ItemType(Enum):
    """物品种类"""
    TOOL = '工具'
    PROP = '道具'
    SEED = '种子'
    VEGETABLE = '蔬菜'
    FRUIT = '水果'
    FLOWER = '花'
    FISH = '鱼'
    COIN = '货币'
    OTHER = '其他'
    ERROR = "未知物品"


class Item:
    """定义物品及其基本数据，注意默认方法无物品消耗，请自行添加"""

    def __init__(self, item_name: str, item_type: ItemType = ItemType.OTHER, item_info='', item_price=0,
                 using_funcs: ItemFuncs = None):
        """
        name 物品名称，要求不为空
        """
        self._name = item_name
        self._type = item_type
        self._info = item_info
        self._price = item_price
        self._using_funcs = using_funcs

    def use_item(self, user_data):
        """在背包使用指定物品"""
        func_message = MessageBuilder()
        try:
            for check in self._using_funcs.checks:
                item_funcs.exec_check(check, user_data)
            user_data.item_num_change(self._name, -1)
            for func in self._using_funcs.funcs:
                func_message.text(item_funcs.exec_func(func, user_data))
        except ConditionNotMet as e:
            return func_message.text(f'{e.prompt}')
        return func_message

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
        if self._using_funcs is None:
            return False
        return True

    def get_item_type(self) -> ItemType:
        """获取物品的类型的ItemType对象"""
        return self._type

    def get_use_funcs(self) -> ItemFuncs | None:
        """获取该物品在使用时执行的方法"""
        return self._using_funcs

    def set_use_funcs(self, funcs: ItemFuncs):
        """设置该物品在使用时执行的方法"""
        self._using_funcs = funcs


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
        """返回是否已注册指定物品"""
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
"""所有物品数据"""


class ItemMeta:
    """物品数据"""

    def __init__(self, item_meta: dict[str: Any]):
        self.num: int = item_meta.pop("num", 0)
        self.extra: dict = item_meta

    def meta_to_dict(self) -> dict[str: Any]:
        """转换成字典"""
        meta = self.extra
        meta["num"] = self.num
        return meta

    def to_meta(self) -> "ItemMeta":
        """获取ItemMeta"""
        return self


class ToolItemMeta(ItemMeta):
    """为工具类设计的ItemMeta，通过ItemMeta转化获得"""

    def __init__(self, meta: ItemMeta):
        self.damage = meta.extra.pop("damage", 0)
        super().__init__(meta.meta_to_dict())

    def meta_to_dict(self) -> dict[str: Any]:
        meta = self.extra
        meta["num"] = self.num
        meta["damage"] = self.damage
        return meta

    def to_meta(self) -> ItemMeta:
        return ItemMeta(self.meta_to_dict())


Meta = TypeVar('Meta', bound=ItemMeta)


class ItemStack:
    """物品堆：物品的个性化，带有特殊信息。不要轻易构造该类，除非确保item_type符合item"""

    def __init__(self, item_name: str, item_type: ItemType, item_meta: Meta):
        self._name = item_name
        self._type = item_type
        self.meta = item_meta

    def get_name(self):
        """获取物品名"""
        return self._name

    def get_type(self):
        """获取物品种类"""
        return self._type


class ToolItemStack(ItemStack):
    def __init__(self, item_stack: ItemStack, max_durable: int):
        super().__init__(item_stack.get_name(), item_stack.get_type(), item_stack.meta)
        self.meta = ToolItemMeta(self.meta)
        self.max_durable = max_durable

    def to_stack(self) -> ItemStack:
        """转化成ItemStack"""
        return ItemStack(self._name, self.get_type(), self.meta.to_meta())

    def add_used_tool(self, damage: int) -> "ToolItemStack":
        """添加一个受损值为 damage 的工具"""
        self.meta.num += 1
        self.meta.damage += damage
        while self.meta.damage >= self.max_durable:
            self.meta.num -= 1
            self.meta.damage -= self.max_durable
        if self.meta.num < 0:
            self.meta.num = 0
        return self

    def get_used_tool(self) -> int | None:
        """拿取一个工具，返回 None 时失败，返回 int 时成功且此值为此工具的受损值"""
        while self.meta.damage >= self.max_durable:
            self.meta.num -= 1
            self.meta.damage -= self.max_durable
        if self.meta.num < 1:
            self.meta.num = 0
            return None
        self.meta.num -= 1
        damage = self.meta.damage
        self.meta.damage = 0
        return damage


Stack = TypeVar('Stack', bound=ItemStack)


class BackPack:
    """背包：里面是通过ItemType分类的物品名称与ItemMeta的字典。以ItemStack形式获取信息。
    bp_dict结构:
        {
            "item_name": {"key": value}
        }"""

    def __init__(self, bp_dict: dict):
        global items
        self._backpack: Dict[ItemType: Dict[str: ItemMeta]] = dict()
        for _type in ItemType:
            self._backpack[_type] = dict()
        for _name in bp_dict.keys():
            _meta = bp_dict[_name]
            if _meta['num'] <= 0:
                continue
            _type = items.get_reg_item_type(_name)
            self._backpack[_type][_name] = ItemMeta(_meta)
        self._modify = False

    def bp_has_item(self, item_name: str) -> bool:
        """背包中是否有指定物品"""
        for _type in ItemType:
            if item_name in self._backpack[_type]:
                if self._backpack[_type][item_name].num > 0:
                    return True
                else:
                    self.remove_item(item_name)
                    break
        return False

    def get_item_stack(self, item_name: str) -> ItemStack:
        """获取背包中指定物品的物品堆，没有则返回新物品堆"""
        for _type in ItemType:
            if item_name in self._backpack[_type]:
                return ItemStack(item_name, _type, self._backpack[_type][item_name])
        return ItemStack(item_name, items.get_reg_item_type(item_name), ItemMeta({}))

    def set_item_with_stack(self, item_stack: Stack):
        """通过物品堆设置物品"""
        self._modify = True
        _name = item_stack.get_name()
        _type = item_stack.get_type()
        if item_stack.meta.num <= 0:
            self.remove_item(_name)
        else:
            self._backpack[_type][_name] = item_stack.meta.to_meta()

    def set_item_with_meta(self, item_name: str, item_meta: dict[str: Any]):
        """通过物品数据设置物品"""
        self._modify = True
        _type = items.get_reg_item_type(item_name)
        if item_meta.get("num", 0) <= 0:
            self.remove_item(item_name)
        else:
            self._backpack[_type][item_name] = ItemMeta(item_meta)

    def set_item(self, item_name: str, num: int):
        """设置指定数量的无其余数据的物品"""
        self._modify = True
        self.set_item_with_meta(item_name, {"num": num})

    def remove_item(self, item_name: str) -> bool:
        """移除背包中的指定物品"""
        self._modify = True
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

    def is_modify(self) -> bool:
        return self._modify
