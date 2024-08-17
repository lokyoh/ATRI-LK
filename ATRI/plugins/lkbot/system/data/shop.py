from ATRI.log import log

from .item import Item, items


class Shop:
    """创建一个商店"""

    def __init__(self, shop_name: str):
        self._name = shop_name
        self._goods_list = []
        self._goods_price = []
        self._goods_coin_type = []
        self._goods_limit = []

    def add_goods(self, item_name: str | Item, price: int, coin_type: str = "ATRI币", limit: int = 0):
        if type(item_name) is Item:
            item_name = item_name.get_item_name()
        else:
            if not items.has_item(item_name):
                log.error(f"{self._name} 在添加物品时出现错误:物品 {item_name} 不存在")
                return
        if price <= 0:
            log.error(f"{self._name} 在添加物品时出现错误:物品 {item_name} 的价格 {price} 非法")
            return
        if not (coin_type == "ATRI币" or items.has_item(coin_type)):
            log.error(f"{self._name} 在添加物品时出现错误:找不到物品 {item_name} 的货币类型 {coin_type}")
            return
        if limit < 0:
            log.error(f"{self._name} 在添加物品时出现错误:物品 {item_name} 的购买数量限制 {limit} 非法")
            return
        self._goods_list.append(item_name)
        self._goods_price.append(price)
        self._goods_coin_type.append(coin_type)
        self._goods_limit.append(limit)
        return self

    def get_shop_name(self) -> str:
        return self._name

    def get_goods_list(self) -> list:
        return self._goods_list

    def get_goods_limit_by_index(self, index) -> int | None:
        if 0 <= index < len(self._goods_limit):
            return self._goods_limit[index]
        return None

    def get_goods_index(self, item_name: str) -> int | None:
        if item_name in self._goods_list:
            return self._goods_list.index(item_name)
        return None

    def get_goods_price_by_index(self, index) -> int | None:
        if 0 <= index < len(self._goods_price):
            return self._goods_price[index]
        return None

    def get_goods_coin_type_by_index(self, index) -> str | None:
        if 0 <= index < len(self._goods_coin_type):
            return self._goods_coin_type[index]
        return None

    def has_goods(self, item_name) -> bool:
        if item_name in self._goods_list:
            return True
        return False

    def clear_goods(self):
        self._goods_list = []
        self._goods_price = []
        self._goods_coin_type = []
        self._goods_limit = []


class ShopRegister:
    def __init__(self):
        self._shop_names = []
        self._shop_list = []

    def register(self, shop: Shop):
        self._shop_names.append(shop.get_shop_name())
        self._shop_list.append(shop)

    def get_shop_by_name(self, shop_name) -> Shop | None:
        if shop_name in self._shop_names:
            return self._shop_list[self._shop_names.index(shop_name)]
        return None

    def has_shop(self, shop_name):
        if shop_name in self._shop_names:
            return True
        return False

    def get_shop_names(self):
        return self._shop_names

    def shops_clear(self):
        self._shop_names = []
        self._shop_list = []


shops = ShopRegister()
