from datetime import date
import os
from random import randint

from ATRI.system.lkapi.entity.user import UserData

from .crop import crop_data_list, CropData


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
        self.fertilization_date = data.get("fertilization_date", "")
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

    def is_out_season(self):
        if self.state != 0:
            if self.crop in crop_data_list:
                if not crop_data_list[self.crop].growable(date.today().month):
                    self.water = 0
                    self.crop = ""
                    self.fertilizer = ""
                    return True
        return False

    def water_change(self, rainy: bool):
        if rainy:
            if self.state != 0:
                self.water = 1
                if self.crop != "":
                    self.days += 1
        elif self.water == 1:
            self.water = 0
            if self.crop != "":
                self.days += 1
        else:
            if self.crop == "":
                if randint(0, 100) < 50 and self.state == 1:
                    self.state = 0

    def seeding(self, crop: str):
        self.crop = crop
        self.days = 0
        self.harvest = False

    def watering(self):
        self.water = 1
        self.water_date = str(date.today())

    def harvesting(self, user_data: UserData, crop_data: CropData):
        if crop_data.is_lasting():
            self.harvest = True
        else:
            self.crop = ""
            self.harvest = False
        self.days = 0
        harvest_list = crop_data.get_harvest_list()
        for item in harvest_list.keys():
            user_data.item_num_change(item, harvest_list[item])

    def hoeing(self):
        self.state = 1

    def get_crop(self) -> CropData | None:
        if self.crop not in crop_data_list:
            return None
        return crop_data_list[self.crop]

    def get_state(self) -> int | None:
        crop = self.get_crop()
        if not crop:
            return None
        return crop.get_stage(self.days, self.harvest)

    def crop_can_harvest(self) -> bool:
        crop = self.get_crop()
        if not crop:
            return False
        return crop.can_harvest(self.days, self.harvest)

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
            content += f'<img width="48px" src="{url}"/><br/>'
        if self.water == 0:
            content += "`未浇水`"
        else:
            content += "~~已浇水~~"
        return content

    def is_modify(self):
        return self._modify

    def __setattr__(self, key, value):
        if not key.startswith('_'):
            self._modify = True
        super().__setattr__(key, value)
