import os
import re
from datetime import date
from enum import Enum
from pathlib import Path
from random import randint
import yaml

from ATRI.log import log
from ATRI.system.lkapi.entity.item import items, Item, ItemType
from ATRI.exceptions import str_traceback

from .farm_shop import farm_shop
from .season import Season, Month


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

    def is_lasting(self) -> bool:
        if self._lasting == 0:
            return False
        return True

    def get_harvest_list(self) -> list:
        harvest_list = []
        for i in range(len(self._harvest_list[0])):
            item = self._harvest_list[0][i]
            match_plus = re.match(r"(.*)\+$", item)
            if match_plus:
                item = match_plus[1]
                while randint(1, 100) <= self._harvest_list[1][i]:
                    harvest_list.append(item)
            else:
                if randint(1, 100) <= self._harvest_list[1][i]:
                    harvest_list.append(item)
        return harvest_list

    def get_growth_stage(self):
        return self._growth_stage

    def get_lasting(self):
        return self._lasting

    def get_harvest_exp(self) -> int:
        return self._exp


crop_data_list: dict[str, CropData] = {}


def load_crop_data(loader_name: str, path: Path):
    global crop_data_list
    crop_dirs = os.listdir(path)
    crop_count = 0
    shop_count = 0
    for crop_dir in crop_dirs:
        try:
            conf = yaml.safe_load((path / crop_dir / "data.yml").read_bytes())
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
                farm_shop.add_goods(seed, crop_data.get_seed_price())
                shop_count += 1
            crop_data_list[crop_name] = crop_data
            crop_count += 1
        except Exception as e:
            log.error(f"加载作物失败:\n{str_traceback(e)}")
    log.success(f"{loader_name}共加载{crop_count}种作物")
    log.success(f"{loader_name}共有{shop_count}种作物上架商店")
