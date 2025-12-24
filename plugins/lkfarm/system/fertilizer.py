import math
from typing import Callable

from ATRI.log import log
from ATRI.system.lkapi.entity.item import items, Item, ItemType

from .farm_field import FieldData
from .farm_shop import farm_shop

fertilizer_effect: dict[str, Callable] = {}


class FertilizerData:
    def __init__(self, data: dict):
        self.name = data["name"]
        self.effect = data["effect"]
        self.intensity = data["intensity"]
        self.price = data["price"]

    def get_effect(self, field: FieldData):
        fertilizer_effect[self.effect](field, self.intensity)


def improve_quality(field: FieldData, intensity: int):
    field.quality = intensity


def increase_growth_rate(field: FieldData, intensity: float):
    new_lasting = int(field.lasting * intensity) + 1
    if new_lasting == field.lasting:
        new_lasting -= 1
    if new_lasting == 0:
        new_lasting = 1
    field.lasting = new_lasting
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


fertilizer_effect['improve_quality'] = improve_quality
fertilizer_effect['increase_growth_rate'] = increase_growth_rate

fertilizer_data_list: dict[str, FertilizerData] = {}

fertilizer_datas = [
    {
        'name': '初级肥料',
        'intro': '稍微提高土壤品质，增加你种出高品质作物的几率。',
        'effect': 'improve_quality',
        'intensity': 30,
        'price': 100,
        'sell': 10
    },
    {
        'name': '高级肥料',
        'intro': '提高土壤品质，增加你种出高品质作物的可能性。',
        'effect': 'improve_quality',
        'intensity': 65,
        'price': 250,
        'sell': 25
    },
    {
        'name': '顶级肥料',
        'intro': '大幅提高土壤品质，增加你种出高品质作物的可能性。',
        'effect': 'improve_quality',
        'intensity': 100,
        'price': 500,
        'sell': 50
    },
    {
        'name': '生长激素',
        'intro': '促进叶子生长。保证能让植物的生长速度加快10%。',
        'effect': 'increase_growth_rate',
        'intensity': 0.1,
        'price': 100,
        'sell': 10
    },
    {
        'name': '高级生长激素',
        'intro': '促进叶子生长。保证能让植物的生长速度加快25%。',
        'effect': 'increase_growth_rate',
        'intensity': 0.25,
        'price': 260,
        'sell': 26
    },
    {
        'name': '顶级生长激素',
        'intro': '促进叶子生长。保证能让植物的生长速度加快33%以上。',
        'effect': 'increase_growth_rate',
        'intensity': 0.33,
        'price': 350,
        'sell': 35
    }
]

def load_fertilizer_data(loader_name: str, data):
    global fertilizer_data_list
    fertilizer_count = 0
    for fertilizer_data in data:
        items.register(Item(
            fertilizer_data['name'],
            ItemType.PROP,
            fertilizer_data['intro'],
            fertilizer_data['sell']
        ))
        fertilizer_data_list[fertilizer_data['name']] = FertilizerData(fertilizer_data)
        fertilizer_count += 1
    log.success(f"{loader_name}共加载{fertilizer_count}种肥料")

def add_fertilizer_to_shop():
    for fertilizer in fertilizer_data_list:
        farm_shop.add_goods(fertilizer, fertilizer_data_list[fertilizer].price)
