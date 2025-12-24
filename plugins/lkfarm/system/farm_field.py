import copy
from datetime import date
import os
from random import randint

from ATRI.log import log
from ATRI.system.lkapi.entity.item import items
from ATRI.system.lkapi.entity.user import UserData

from .crop import crop_data_list, CropData


class FieldData:
    def __init__(self, crop: str = None):
        self.quality = 0
        if crop in crop_data_list:
            c_d = crop_data_list[crop]
            self.growth_stage = copy.copy(c_d.get_growth_stage())
            self.lasting = c_d.get_lasting()
        else:
            self.growth_stage = []
            self.lasting = 0

    def get_stage(self, days, harvest) -> int:
        if not harvest:
            stage_day = 0
            for i in range(len(self.growth_stage)):
                stage_day += self.growth_stage[i]
                if days < stage_day:
                    return i + 1
            return len(self.growth_stage) + 1
        else:
            if days < self.lasting:
                return len(self.growth_stage) + 2
            return len(self.growth_stage) + 1

    def get_growth_days(self) -> int:
        days = 0
        for day in self.growth_stage:
            days += day
        return days

    def can_harvest(self, days: int, harvest: bool) -> bool:
        if harvest:
            if days >= self.lasting:
                return True
            return False
        else:
            if days >= self.get_growth_days():
                return True
            return False


from .fertilizer import fertilizer_data_list


class FarmField:
    def __init__(self, data):
        self.state = data.get("state", 0)
        # 0未耕种 1已耕种
        self.water = data.get("water", 0)
        # 0未浇水 1已浇水
        self.water_date = data.get("water_date", "")
        self.crop = data.get("crop", "")
        self.days = data.get("days", 0)
        self.harvest = data.get("harvest", False)
        self.fertilizer = data.get("fertilizer", "")
        self.data = FieldData(self.crop)
        self.init_data()
        self._modify = False

    def to_field_dic(self):
        return {
            "state": self.state,
            "water": self.water,
            "water_date": self.water_date,
            "crop": self.crop,
            "days": self.days,
            "harvest": self.harvest,
            "fertilizer": self.fertilizer
        }

    def is_out_season(self, is_cross_seasonal: bool = False):
        if is_cross_seasonal:
            self.fertilizer = ''
        if self.state != 0:
            if self.crop in crop_data_list:
                if not crop_data_list[self.crop].growable(date.today().month):
                    self.water = 0
                    self.crop = ""
                    self.data = FieldData()
                    return True
        return False

    def water_change(self, rainy: bool):
        if self.water == 1:
            self.water = 0
            if self.crop != "":
                self.days += 1
        elif self.crop == "" and self.fertilizer == "" and self.state == 1 and randint(1, 100) < 51:
            self.state = 0
        if rainy and self.state != 0:
            self.watering()

    def seeding(self, crop: str):
        self.crop = crop
        self.days = 0
        self.harvest = False
        self.data = FieldData(self.crop)
        self.init_data()

    def watering(self):
        self.water = 1
        self.water_date = str(date.today())

    def harvesting(self, user_data: UserData, crop_data: CropData, f_user_data):
        if crop_data.is_lasting():
            self.harvest = True
        else:
            self.crop = ""
            self.harvest = False
            self.data = FieldData()
        self.days = 0
        harvest_list = crop_data.get_harvest_list()
        level = f_user_data.get_level_exp()[0]
        for item in harvest_list:
            user_data.item_num_change(self._get_item_quality(item, level), 1)

    def hoeing(self):
        self.state = 1

    def fertilization(self, fertilizer: str):
        self.fertilizer = fertilizer

    def get_crop(self) -> CropData | None:
        if self.crop not in crop_data_list:
            return None
        return crop_data_list[self.crop]

    def get_state(self) -> int | None:
        return self.data.get_stage(self.days, self.harvest)

    def crop_can_harvest(self) -> bool:
        crop = self.get_crop()
        if not crop:
            return False
        return self.data.can_harvest(self.days, self.harvest)

    def to_md(self):
        if self.state == 0:
            return "`未锄地`"
        content = ""
        if self.crop != "":
            url = f"{os.getcwd()}\\res\\data\\lkfarm\\Crop"
            if self.crop in crop_data_list:
                if self.crop_can_harvest():
                    content += "***可收获***<br/>"
                name = crop_data_list[self.crop].get_crop_name()
                stage = self.get_state()
                url = f"{url}\\{name}\\{name}_Stage_{stage}.png"
            content += f'<img width="48px" src="{url}"/>'
        if self.water == 0:
            content += "<br/>`未浇水`"
        else:
            content += "<br/>~~已浇水~~"
        if self.fertilizer:
            content += f"<br/>{self.fertilizer}"
        return content

    def is_modify(self):
        return self._modify

    def init_data(self):
        if self.crop in crop_data_list and self.fertilizer in fertilizer_data_list:
            fertilizer_data_list[self.fertilizer].get_effect(self.data)

    def _get_item_quality(self, item, level) -> str:
        quality = ''
        if items.has_item(f'{item}-银'):
            r_q = randint(1, 110)
            r_q += randint(0, level * 6)
            r_q += self.data.quality
            if r_q > 300:
                quality = '铱'
            elif r_q > 200:
                quality = '金'
            elif r_q > 100:
                quality = '银'
        if quality and items.has_item(f'{item}-{quality}'):
            return f'{item}-{quality}'
        else:
            if quality:
                log.warning(f'{item}缺失品质{quality}')
            return item

    def __setattr__(self, key, value):
        if not key.startswith('_'):
            self._modify = True
        super().__setattr__(key, value)
