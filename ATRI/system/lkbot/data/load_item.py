import os
import yaml
from pathlib import Path

from ATRI import RES_DIR
from ATRI.exceptions import InvalidConfigured
from ATRI.log import log

from .item import Item, ItemType, items
from .item_func import ItemFuncs, item_funcs, ItemUsingFunc
from .shop import shops, Shop

ITEM_PATH = RES_DIR / "data" / "item"
SHOP_PATH = RES_DIR / "data" / "shop"


def dict_to_shop(data: dict) -> Shop:
    """将商店从字典转化为Item对象"""
    name = data.get('name', '')
    if name == '':
        raise InvalidConfigured("无效名称")
    return Shop(
        name,
        data.get('info', '')
    )


def dict_to_item(data: dict) -> Item:
    """将物品从字典转化为Item对象"""
    name = data.get('name', '')
    if name == '':
        raise InvalidConfigured("无效名称")
    return Item(
        name,
        ItemType(data.get('type', '其他')),
        data.get('info', ''),
        data.get('price', 0)
    )


def dict_to_funcs(data: dict) -> ItemFuncs:
    _item_funcs = ItemFuncs()
    funcs = data.get('funcs', [])
    checks = data.get('checks', [])
    for func in funcs:
        name = func['name']
        item_funcs.check_func(name)
        args = func.get('args', '')
        _item_funcs.funcs.append(ItemUsingFunc(name, args))
    for check in checks:
        name = check['name']
        item_funcs.check_func(name, 'check')
        args = check.get('args', '')
        _item_funcs.checks.append(ItemUsingFunc(name, args))
    return _item_funcs


def load_shops(shop_file: Path):
    """加载指定文件中的商店数据"""
    shops_data: dict = yaml.safe_load(shop_file.read_bytes())
    if shops_data is None:
        return
    for key in shops_data.keys():
        try:
            data = shops_data[key]
            shop = dict_to_shop(data)
            shops.register(shop)
        except Exception as e:
            log.warning(f'{shop_file}-{key}无效商店配置:{e.args}')


def load_items(item_file: Path):
    """加载指定文件中的物品数据"""
    items_data: dict = yaml.safe_load(item_file.read_bytes())
    if items_data is None:
        return
    for key in items_data.keys():
        try:
            # 物品注册
            data = items_data[key]
            item = dict_to_item(data)
            # 功能注册(可选)
            func_data = data.get('func', None)
            if func_data:
                item.set_use_funcs(dict_to_funcs(func_data))
            items.register(item)
            # 商品注册(可选)
            shop_data = data.get('shop', None)
            if shop_data:
                shops.get_shop_by_name(shop_data['name']).add_goods(item, shop_data['price'], shop_data['type'])
        except Exception as e:
            log.warning(f'{item_file}-{key}无效物品配置:{e.args}')


def auto_load_items():
    """自动加载本地文件上的商店与物品数据"""
    shop_files = os.listdir(SHOP_PATH)
    for shop_file in shop_files:
        load_shops(SHOP_PATH / shop_file)
    item_files = os.listdir(ITEM_PATH)
    for item_file in item_files:
        load_items(ITEM_PATH / item_file)
