from datetime import date

from ATRI.system.lkapi.entity.shop import Shop

from .season import Month

def get_farm_shop_info():
    return f"这是亚托莉小店售卖农场用品的地方,现在正在出售{Month(date.today().month).to_season().value}的种子,快来看看吧。"

farm_shop = Shop("农场商店", get_farm_shop_info())
