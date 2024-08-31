import os
import re
from datetime import date
from enum import Enum
from random import randint
import yaml

from ATRI import RES_DIR
from ATRI.log import log
from ATRI.system.lkbot.data.item import items, Item, ItemType
from ATRI.system.lkbot.data.shop import Shop

FARM_RES_PATH = RES_DIR / "lkfarm"


class Season(Enum):
    """春3-5，夏6-8，秋9-11，冬12-2"""
    SPRING = "春季"
    SUMNER = "夏季"
    AUTUMN = "秋季"
    WINTER = "冬季"
    SPRSUM = "春夏两季"
    SUMAUT = "夏秋两季"
    SPSUAU = "春夏秋三季"
    ALL = "全季"

    def get_seasons(self):
        if self == Season.SPRSUM:
            return [Season.SPRING, Season.SUMNER]
        if self == Season.SUMAUT:
            return [Season.SUMNER, Season.AUTUMN]
        if self == Season.SPSUAU:
            return [Season.SPRING, Season.SUMNER, Season.AUTUMN]
        if self == Season.ALL:
            return [Season.SPRING, Season.SUMNER, Season.AUTUMN, Season.WINTER]
        return [self]


class Month(Enum):
    JANUARY = 1
    FEBRUARY = 2
    MARCH = 3
    APRIL = 4
    MAY = 5
    JUNE = 6
    JULY = 7
    AUGUST = 8
    SEPTEMBER = 9
    OCTOBER = 10
    NOVEMBER = 11
    DECEMBER = 12

    def to_season(self) -> Season:
        if 3 <= self.value <= 5:
            return Season.SPRING
        elif 6 <= self.value <= 8:
            return Season.SUMNER
        elif 9 <= self.value <= 11:
            return Season.AUTUMN
        else:
            return Season.WINTER


class CropType(Enum):
    VEGETABLE = "蔬菜"
    FRUIT = "水果"
    FLOWER = "花"
    SEED = "种子"

    def is_seed(self) -> bool:
        if self == CropType.SEED:
            return True
        return False


class CropData:
    def __init__(self, name: str, data: dict):
        self._name = name
        self._season = Season(data["season"])
        self._type = CropType(data["type"])
        self._seed_price = data["price"].get("seed", 0)
        self._growth_stage = data["growth"]["stage"]
        self._lasting = data["growth"].get("lasting", 0)
        self._harvest_list = data["harvest_list"]
        self._exp = data["exp"]

    def get_crop_name(self) -> str:
        return self._name

    def growable(self, month: Month | int) -> bool:
        if type(month) is int:
            month = Month(month)
        if month.to_season() in self._season.get_seasons():
            return True
        return False

    def crop_is_seed(self) -> bool:
        return self._type.is_seed()

    def get_seed_price(self) -> int:
        return self._seed_price

    def get_growth_days(self) -> int:
        days = 0
        for day in self._growth_stage:
            days += day
        return days

    def can_harvest(self, days: int, harvest: bool) -> bool:
        if harvest:
            if days >= self._lasting:
                return True
            return False
        else:
            if days >= self.get_growth_days():
                return True
            return False

    def is_lasting(self) -> bool:
        if self._lasting == 0:
            return False
        return True

    def get_harvest_list(self) -> dict:
        harvest_list = {}
        for i in range(len(self._harvest_list[0])):
            item = self._harvest_list[0][i]
            match_plus = re.match(r"(.*)\+$", item)
            if match_plus:
                item = match_plus[1]
                while randint(0, 100) <= self._harvest_list[1][i]:
                    if item in harvest_list:
                        harvest_list[item] += 1
                    else:
                        harvest_list[item] = 1
                continue
            if randint(0, 100) <= self._harvest_list[1][i]:
                if item in harvest_list:
                    harvest_list[item] += 1
                else:
                    harvest_list[item] = 1
        return harvest_list

    def get_stage(self, days, harvest) -> int:
        if not harvest:
            stage_day = 0
            for i in range(len(self._growth_stage)):
                stage_day += self._growth_stage[i]
                if days < stage_day:
                    return i + 1
            return len(self._growth_stage) + 1
        else:
            if days < self._lasting:
                return len(self._growth_stage) + 2
            return len(self._growth_stage) + 1

    def get_harvest_exp(self) -> int:
        return self._exp


seed_shop = Shop("种子商店",
                 f"这是亚托莉小店售卖种子的地方,现在正在出售{Month(date.today().month).to_season().value}的种子,快来看看吧。")

crop_data_list = {}


def load_crop_data():
    global crop_data_list
    crop_dirs = os.listdir(FARM_RES_PATH / "Crop")
    for crop_dir in crop_dirs:
        try:
            conf = yaml.safe_load((FARM_RES_PATH / "Crop" / crop_dir / "data.yml").read_bytes())
            crop_data = CropData(crop_dir, conf)
            crop_name = conf["name"]
            crop_intro = conf.get("intro", "无介绍")
            crop_price = conf["price"]["crop"]
            days = crop_data.get_growth_days()
            lasting = conf['growth'].get('lasting', 0)
            seed_intro = f"{Season(conf['season']).value}种植，{days}天后收获{'' if lasting == 0 else f',之后每{lasting}天收获一次'}。"
            if crop_data.crop_is_seed():
                seed = Item(f"{crop_name}", ItemType.SEED, crop_intro, crop_price)
                seed2 = Item(f"{crop_name}-银", ItemType.SEED, crop_intro, int(crop_price * 1.25))
                seed3 = Item(f"{crop_name}-金", ItemType.SEED, crop_intro, int(crop_price * 1.5))
                seed4 = Item(f"{crop_name}-铱", ItemType.SEED, crop_intro, crop_price * 2)
                items.register(seed).register(seed2).register(seed3).register(seed4)
            else:
                seed_sell = conf["price"]["seed_sell"]
                seed = Item(f"{crop_name}种子", ItemType.SEED, seed_intro, seed_sell)
                _type = ItemType(conf["type"])
                crop = Item(f"{crop_name}", _type, crop_intro, crop_price)
                crop2 = Item(f"{crop_name}-银", _type, crop_intro, int(crop_price * 1.25))
                crop3 = Item(f"{crop_name}-金", _type, crop_intro, int(crop_price * 1.5))
                crop4 = Item(f"{crop_name}-铱", _type, crop_intro, crop_price * 2)
                items.register(seed).register(crop).register(crop2).register(crop3).register(crop4)
            if crop_data.growable(date.today().month) and crop_data.get_seed_price() != 0:
                seed_shop.add_goods(seed, crop_data.get_seed_price())
            crop_data_list[crop_name] = crop_data
        except Exception as e:
            log.error(f"{e}: {e.args}")
    log.success(f"共加载{len(crop_data_list)}种作物")
    log.success(f"共{len(seed_shop.get_goods_list())}种作物上架商店")
