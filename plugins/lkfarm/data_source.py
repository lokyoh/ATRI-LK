import os
import re
import zlib
from datetime import date, timedelta, datetime

from nonebot.adapters.onebot.v11 import Event
from nonebot.internal.matcher import Matcher
from nonebot.internal.params import Depends

from ATRI.dir import RES_DIR, TEMP_DIR
from ATRI.system.htmlrender import md_to_pic
from ATRI.system.lkapi.bot import util as lk_util
from ATRI.system.lkapi.bot.events import (
    item_loading_events,
    sign_in_events,
    SignInEvent,
    daily_update_event,
    user_info_events,
    UserInfoEvent,
)
from ATRI.system.lkapi.entity.item import items, ItemType
from ATRI.system.lkapi.entity.shop import shops
from ATRI.system.lkapi.entity.user import UserData
from ATRI.utils.sqlite import DataBase
from ATRI.log import log

from .system.crop import load_crop_data, crop_data_list, CropData, Month, Season
from .system.farm_shop import farm_shop, get_farm_shop_info
from .system.farm_user import user_farm_datas, UserFarmData, get_user_farm_data
from .system.fertilizer import load_fertilizer_data, add_fertilizer_to_shop
from .system.forecast import weather_forecast
from .system.weather import get_weather
from . import config, plugin

_config = config.config()

plugin.scheduler_jobs().add_job(weather_forecast, "农场天气预报", 'cron', hour=_config.hour, minute=_config.minute)

FARM_RES_PATH = RES_DIR / 'data' / "lkfarm"
FARM_TEMP_PATH = TEMP_DIR / 'farm'


@item_loading_events.handle()
def lkfarm_item_loading():
    farm_shop.clear_goods()
    load_fertilizer_data()
    add_fertilizer_to_shop()
    load_crop_data('Core', FARM_RES_PATH / "Crop")
    shops.register(farm_shop)


@daily_update_event.handle()
def lkfarm_farm_daily_update():
    log.info("开始更新农场数据")
    db = DataBase("lkbot.db")

    def daily_up_date():
        _today_weather = user_farm_datas.weather
        user_farm_datas.weather = user_farm_datas.next_weather
        _next_day = date.today() + timedelta(days=1)
        user_farm_datas.next_weather = get_weather(_next_day.month, _next_day.day, _today_weather)
        db.get_exist_table("LKFARM").update(
            f"DATE = '{date.today()}', WEATHER = {user_farm_datas.weather}, NEXT_WEATHER = {user_farm_datas.next_weather}",
            f"DATE != '{date.today()}'")

    daily_up_date()
    db.disconnect()
    month = date.today().month
    if date.today().day == 1 and month % 3 == 0:
        log.info("开始更新种子商店")
        farm_shop.clear_goods()
        add_fertilizer_to_shop()
        for name in crop_data_list:
            crop_data: CropData = crop_data_list[name]
            if crop_data.growable(month) and crop_data.get_seed_price() != 0:
                farm_shop.add_goods(name if crop_data.crop_is_seed() else f"{name}种子", crop_data.get_seed_price())
        farm_shop.set_shop_info(get_farm_shop_info())
        log.success(f"种子商店更新完成，共{len(farm_shop.get_goods_list())}种子上架")
    log.success("农场数据更新成功")


@sign_in_events.handle()
def lkfarm_sign_in(event: SignInEvent):
    season = Month(date.today().month).to_season()
    item = ""
    if season == Season.SPRING:
        item = "胡萝卜种子"
    elif season == Season.SUMMER:
        item = "金皮西葫芦种子"
    elif season == Season.AUTUMN:
        item = "西蓝花种子"
    elif season == Season.WINTER:
        item = "霜瓜种子"
    if items.has_item(item):
        lk_util.item_change_func(event.user_data, item, 1)
        event.add_result(f"获得1个{item}。")
    else:
        log.error(f"{item}没有注册进物品。")
        event.add_result(f"{item}没有注册进物品。")


@user_info_events.handle()
def lkfarm_user_info(event: UserInfoEvent):
    user_id = str(event.user_data.id)
    user_farm_datas.has_user(user_id)
    event.add_result(f'体力:{get_user_farm_data(user_id).endurance}')


class FarmSystem:
    _FARM_MODEL = '''# {name}的农场
> 天气:{weather} 明日:{next_weather}  
> 等级:{level} 经验:{exp} 体力:{endur}

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
    WEATHER = ["晴", "雨", "雷雨", "雪"]

    @staticmethod
    async def check_user(matcher: Matcher, event: Event):
        user_id = event.get_user_id()
        if not lk_util.is_valid_user(user_id):
            await matcher.finish(lk_util.bind_tip)
        user_farm_datas.has_user(user_id)

    async def farm_info(self, user_id):
        user_id = str(user_id)
        fields = []
        user_data = get_user_farm_data(user_id)
        for field in user_data.fields:
            fields.append(field.to_md())
        level, level_exp = user_data.get_level_exp()
        farm_md = self._FARM_MODEL.format(
            today_date=date.today(),
            weather=self.WEATHER[user_farm_datas.weather],
            next_weather=self.WEATHER[user_farm_datas.next_weather],
            endur=user_data.endurance,
            field=fields,
            name=lk_util.get_name(user_id),
            level=level,
            exp=level_exp,
        )
        f_hash = format(zlib.crc32(farm_md.encode()) & 0xFFFFFFFF, '08x')
        f_path_hash = FARM_TEMP_PATH / f'{user_id}.hash'
        f_path_img = FARM_TEMP_PATH / f'{user_id}.png'
        if f_path_img.exists() and date.fromtimestamp(os.path.getmtime(f_path_img)) == datetime.now().date():
            if f_path_hash.exists():
                with open(f_path_hash, 'r') as f:
                    old_hash = f.read()
                if old_hash == f_hash:
                    with open(f_path_img, 'rb') as f:
                        return f.read()
        FARM_TEMP_PATH.mkdir(parents=True, exist_ok=True)
        with open(f_path_hash, 'w') as f:
            f.write(f_hash)
        f_bytes = await md_to_pic(farm_md)
        with open(f_path_img, 'wb') as f:
            f.write(f_bytes)
        return f_bytes

    @staticmethod
    def get_positions(text) -> list:
        p_list = []
        match = re.match(r"(?: ?[A-D][1-8][-_][A-D][1-8]| ?[A-D][1-8])+$", text)
        if match:
            position = match[0]
            m_match = re.findall(r"[A-D][1-8][-_][A-D][1-8]", position)
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
    def seeding(f_user_data: UserFarmData, location: str, crop, user_data: UserData):
        row = location[0]
        line = int(location[1])
        item = items.get_item_by_name(crop)
        if item is None:
            return f"没有物品 {crop} 的物品数据"
        if item.get_item_type() != ItemType.SEED:
            return f"{crop} 的类型是 {item.get_item_type()} 不是种子"
        crop = re.match(r"(.*)种子", crop)[1]
        r, m = f_user_data.seeding(row, line, crop, user_data)
        if r:
            return None
        return m

    @staticmethod
    def hoeing(f_user_data: UserFarmData, location):
        row = location[0]
        line = int(location[1])
        r, m = f_user_data.hoeing(row, line)
        if r:
            m = None
        return m

    @staticmethod
    def watering(f_user_data: UserFarmData, location):
        row = location[0]
        line = int(location[1])
        r, m = f_user_data.watering(row, line)
        if r:
            m = None
        return m

    @staticmethod
    def harvesting(f_user_data: UserFarmData, location, user_data: UserData):
        row = location[0]
        line = int(location[1])
        r, m = f_user_data.harvesting(row, line, user_data)
        if r:
            m = None
        return m

    @staticmethod
    def fertilization(f_user_data: UserFarmData, location, fertilizer, user_data):
        row = location[0]
        line = int(location[1])
        item = items.get_item_by_name(fertilizer)
        if item is None:
            return f"没有物品 {fertilizer} 的物品数据"
        r, m = f_user_data.fertilization(row, line, fertilizer, user_data)
        if r:
            return None
        return m

    @staticmethod
    def c_remove(f_user_data: UserFarmData, location):
        row = location[0]
        line = int(location[1])
        r, m = f_user_data.c_remove(row, line)
        if r:
            m = None
        return m

    @staticmethod
    def easy_operation(f_user_data: UserFarmData, user_data: UserData):
        for i, field in enumerate(f_user_data.fields):
            row = chr(ord('A') + int(i / 8))
            line = i % 8 + 1
            if field.state == 0:
                f_user_data.hoeing(row, line)
            if field.state == 1 and field.water == 0:
                f_user_data.watering(row, line)
            if field.state == 1 and field.crop in crop_data_list:
                if field.crop_can_harvest():
                    f_user_data.harvesting(row, line, user_data)


farm_system = FarmSystem()

CheckFarmUser = Depends(farm_system.check_user)
