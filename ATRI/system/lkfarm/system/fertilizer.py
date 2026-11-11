import math
import os
from typing import Callable

import yaml

from ATRI.dir import RES_DATA_DIR
from ATRI.exceptions import str_traceback
from ATRI.log import log
from ATRI.system.lkapi.entity.item import items, Item, ItemType

from .farm_field import FieldData
from .farm_shop import farm_shop

fertilizer_effect: dict[str, Callable] = {}


class FertilizerData:
    def __init__(self, data: dict):
        self.name: str = data["name"]
        self.effect: str = data["effect"]
        self.intensity: any = data["intensity"]
        self.price: int = data.get("price", 0)

    def get_effect(self, field: FieldData):
        fertilizer_effect[self.effect](field, self.intensity)


def improve_quality(field: FieldData, intensity: int):
    field.quality = intensity


fertilizer_effect['improve_quality'] = improve_quality


def increase_growth_rate(field: FieldData, intensity: float):
    growth_days = 0
    for day in field.growth_stage:
        growth_days += day
    days = math.ceil(growth_days * intensity)
    while days > 0:
        max_index = 1 if field.growth_stage[0] == 1 else 0
        for i in range(1, len(field.growth_stage)):
            if field.growth_stage[i] > field.growth_stage[max_index]:
                max_index = i
        field.growth_stage[max_index] -= 1
        days -= 1


fertilizer_effect['increase_growth_rate'] = increase_growth_rate

fertilizer_data_list: dict[str, FertilizerData] = {}
can_sell_fertilizer: list = []


def load_fertilizer_data():
    fertilizer_path = RES_DATA_DIR / 'lkfarm' / 'Fertilizer'
    global fertilizer_data_list
    global can_sell_fertilizer
    fertilizer_files = os.listdir(fertilizer_path)
    fertilizer_count = 0
    for fertilizer_file in fertilizer_files:
        fertilizer_datas = yaml.safe_load((fertilizer_path / fertilizer_file).read_bytes())
        if fertilizer_datas is None:
            continue
        for key in fertilizer_datas:
            try:
                fertilizer_data = fertilizer_datas[key]
                f_name = fertilizer_data['name']
                items.register(Item(
                    f_name,
                    ItemType.PROP,
                    fertilizer_data['intro'],
                    fertilizer_data['sell']
                ))
                fertilizer_data_list[f_name] = FertilizerData(fertilizer_datas[key])
                if fertilizer_data.get('price', 0) > 0:
                    can_sell_fertilizer.append(f_name)
                fertilizer_count += 1
            except Exception as e:
                log.error(f'Fertilizer/{fertilizer_file}/{key}无效肥料配置:\n{str_traceback(e)}')
    log.success(f"共加载{fertilizer_count}种肥料")


def add_fertilizer_to_shop():
    for fertilizer in can_sell_fertilizer:
        farm_shop.add_goods(fertilizer, fertilizer_data_list[fertilizer].price)
