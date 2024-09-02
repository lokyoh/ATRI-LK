import os
import re
from datetime import date

from ATRI.system.htmlrender import md_to_pic
from ATRI.system.lkbot.util import item_loading_event, lk_util, sign_in_event
from ATRI.system.lkbot.data.item import items, ItemType
from ATRI.system.lkbot.data.shop import shops
from ATRI.system.lkbot.tools.daily_update import daily_update_event
from ATRI.log import log

from .system.crop import load_crop_data, seed_shop, crop_data_list, CropData, Month, Season
from .system.farm_user import user_farm_data


@item_loading_event.handle()
def _():
    load_crop_data()
    shops.register(seed_shop)


@daily_update_event.handle()
def _():
    month = date.today().month
    if date.today().day == 1 and month % 3 == 0:
        log.info("开始更新种子商店")
        seed_shop.clear_goods()
        for name in crop_data_list:
            crop_data: CropData = crop_data_list[name]
            if crop_data.growable(month) and crop_data.get_seed_price() != 0:
                seed_shop.add_goods(name if crop_data.crop_is_seed() else f"{name}种子",
                                    crop_data.get_seed_price())
        seed_shop.set_shop_info(
            f"这是亚托莉小店售卖种子的地方,现在正在出售`{Month(month).to_season().value}`的种子,快来看看吧。")
        log.success(f"种子商店更新完成，共{len(seed_shop.get_goods_list())}种子上架")


@sign_in_event.handle()
def _(user_id):
    season = Month(date.today().month).to_season()
    item = ""
    if season == Season.SPRING:
        item = "胡萝卜种子"
    elif season == Season.SUMNER:
        item = "金皮西葫芦种子"
    elif season == Season.AUTUMN:
        item = "西蓝花种子"
    elif season == Season.WINTER:
        item = "霜瓜种子"
    if items.has_item(item):
        lk_util.item_change(user_id, item, 1)
        return f"获得1个{item}。"
    else:
        return f"{item}没有注册进物品。"


class FarmSystem:
    _FARM_MODEL = '''# {name}的农场
> 体力:{endur} 天气:{weather} 明日:{next_weather}

|农场|A|B|C|D|
|:-:|:-:|:-:|:-:|:-:|
|1|{field[0]}|{field[8]}|{field[16]}|{field[24]}|
|2|{field[1]}|{field[9]}|{field[17]}|{field[25]}|
|3|{field[2]}|{field[10]}|{field[18]}|{field[26]}|
|4|{field[3]}|{field[11]}|{field[19]}|{field[27]}|
|5|{field[4]}|{field[12]}|{field[20]}|{field[28]}|
|6|{field[5]}|{field[13]}|{field[21]}|{field[29]}|
|7|{field[6]}|{field[14]}|{field[22]}|{field[30]}|
|8|{field[7]}|{field[15]}|{field[23]}|{field[31]}|

> {today_date}
'''
    WEATHER = ["晴", "雨", "雪"]

    @staticmethod
    def is_valid_farm_user(user_id):
        user_id = str(user_id)
        return user_farm_data.has_user(user_id)

    async def check_user(self, matcher, event):
        if not self.is_valid_farm_user(event.user_id):
            await matcher.finish("请先使用 /farm.新农场 新建个农场")

    async def farm_info(self, user_id):
        user_id = str(user_id)
        fields = []
        user_data = user_farm_data.get_farm_data(user_id)
        for field in user_data.field:
            if field.state == 0:
                fields.append("`未锄地`")
                continue
            content = ""
            if field.crop != "":
                url = f"{os.getcwd()}\\res\\lkfarm\\Crop"
                if field.crop in crop_data_list:
                    if crop_data_list[field.crop].can_harvest(field.days, field.harvest):
                        content += "***可收获***<br/>"
                    name = crop_data_list[field.crop].get_crop_name()
                    stage = crop_data_list[field.crop].get_stage(field.days, field.harvest)
                    url = f"{url}\\{name}\\{name}_Stage_{stage}.png"
                content += f'<img width="48px" src="{url}"/><br/>'
            if field.water == 0:
                content += "`未浇水`"
            else:
                content += "~~已浇水~~"
            fields.append(content)
        return await md_to_pic(
            self._FARM_MODEL.format(
                today_date=date.today(),
                weather=self.WEATHER[user_farm_data.weather],
                next_weather=self.WEATHER[user_farm_data.next_weather],
                endur=user_data.endurance,
                field=fields,
                name=lk_util.get_name(user_id)
            )
        )

    @staticmethod
    def new_farm(user_id):
        user_id = str(user_id)
        return user_farm_data.new_farm_user(user_id)

    @staticmethod
    def get_positions(text) -> list:
        p_list = []
        match = re.match(r"(?: ?[A-D][1-8]-[A-D][1-8]| ?[A-D][1-8])+$", text)
        if match:
            position = match[0]
            m_match = re.findall(r"[A-D][1-8]-[A-D][1-8]", position)
            for m in m_match:
                position = position.replace(str(m), "")
                start_x = ord(min(m[0], m[3]))
                start_y = ord(min(m[1], m[4]))
                end_x = ord(max(m[0], m[3]))
                end_y = ord(max(m[1], m[4]))
                for i in range(start_x, end_x + 1):
                    for j in range(start_y, end_y + 1):
                        p = chr(i) + chr(j)
                        if not p in p_list:
                            p_list.append(p)
            p_match = re.findall(r"[A-D][1-8]", position)
            for p in p_match:
                if not p in p_list:
                    p_list.append(p)
        return p_list

    @staticmethod
    def seeding(user_id, location: str, crop):
        user_id = str(user_id)
        row = location[0]
        line = int(location[1])
        item = items.get_item_by_name(crop)
        if item is None:
            return f"没有物品 {crop} 的物品数据"
        if item.get_item_type() != ItemType.SEED:
            return f"{crop} 的类型是 {item.get_item_type()} 不是种子"
        crop = re.match(r"(.*)种子", crop)[1]
        r, m = user_farm_data.seeding(user_id, row, line, crop)
        if r:
            return None
        return m

    @staticmethod
    def hoeing(user_id, location):
        user_id = str(user_id)
        row = location[0]
        line = int(location[1])
        r, m = user_farm_data.hoeing(user_id, row, line)
        if r:
            m = None
        return m

    @staticmethod
    def watering(user_id, location):
        user_id = str(user_id)
        row = location[0]
        line = int(location[1])
        r, m = user_farm_data.watering(user_id, row, line)
        if r:
            m = None
        return m

    @staticmethod
    def harvesting(user_id, location):
        user_id = str(user_id)
        row = location[0]
        line = int(location[1])
        r, m = user_farm_data.harvesting(user_id, row, line)
        if r:
            m = None
        return m


farm_system = FarmSystem()
