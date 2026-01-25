import json
from datetime import date, datetime, timedelta
from random import choices, randint
from typing import List

from ATRI.log import log
from ATRI.utils.lock import GroupLock
from ATRI.system.lkapi.entity.user import UserData

from .crop import crop_data_list, CropData
from .datebase import farm_table, user_table
from .farm_field import FarmField
from .weather import get_weather
from .fertilizer import fertilizer_data_list
from .season import Month


class UserFarmData:
    def __init__(self, data):
        self.id = data[0]
        self.date = datetime.strptime(data[1], '%Y-%m-%d').date()
        self.endurance = data[2]
        self.lucky = data[3]
        self.exp = data[4]
        self.fields: List[FarmField] = []
        for i in range(5, len(data), 1):
            self.fields.append(FarmField(json.loads(data[i])))
        self._modify = False

    def endurance_change(self, num) -> bool:
        num = self.endurance + num
        if num < 0:
            return False
        self.endurance = num
        log.debug(f"用户{self.id}体力变化{num},现值{self.endurance}")
        return True

    def lucky_change(self, num):
        num = self.lucky + num
        if num > 100:
            num = 100
        if num < -100:
            num = -100
        self.lucky = num
        log.debug(f"用户{self.id}运气变化{num},现值{self.endurance}")

    def get_trans_lucky(self) -> str:
        return self.lucky

    def exp_change(self, num):
        num = self.exp + num
        if num < 0:
            return False
        self.exp = num
        log.debug(f"用户{self.id}经验变化{num},现值{self.endurance}")
        return True

    def get_level_exp(self) -> tuple[int, int]:
        level = 0
        level_exp = self.exp
        while level_exp >= (level + 1) * 1000:
            level_exp -= (level + 1) * 1000
            level += 1
        return level, level_exp

    def get_field(self, row, line) -> FarmField:
        return self.fields[(ord(row) - ord('A')) * 8 + line - 1]

    def seeding(self, row: str, line: int, crop: str, user_data: UserData) -> tuple[bool, str | None]:
        """请检查种子是否存在"""
        data: FarmField = self.get_field(row, line)
        if data.state == 0:
            return False, f"请先锄地"
        if data.crop != "" or data.state == 0:
            return False, f"已经有作物了"
        if not crop in crop_data_list:
            return False, f"{crop} 的作物数据未找到"
        month = date.today().month
        if not crop_data_list[crop].growable(month):
            return False, f"{crop} 不能在{Month(month).to_season().value()}播种"
        if not user_data.item_num_change(f"{crop}种子", -1):
            return False, f"背包中没有 {crop} 种子"
        data.seeding(crop)
        log.debug(f"{self.id}在{row}{line}位置种植{crop}")
        return True, None

    def watering(self, row: str, line: int) -> tuple[bool, str | None]:
        data: FarmField = self.get_field(row, line)
        if data.state == 0:
            return False, f"请先锄地"
        if data.water == 1:
            return False, f"已经浇水了"
        if not self.endurance_change(-20):
            return False, "体力不足"
        data.watering()
        log.debug(f"{self.id}在{row}{line}位置浇水")
        return True, None

    def harvesting(self, row: str, line: int, user_data: UserData) -> tuple[bool, str | None]:
        data: FarmField = self.get_field(row, line)
        if data.state == 0:
            return False, f"请先锄地"
        if data.crop == "":
            return False, f"没有作物"
        if not data.crop in crop_data_list:
            return False, f"未知作物"
        if not data.crop_can_harvest():
            return False, f"不可收获"
        crop_data: CropData = crop_data_list[data.crop]
        data.harvesting(user_data, crop_data, self)
        self.exp_change(crop_data.get_harvest_exp())
        log.debug(f"{self.id}收获了{row}{line}位置的农作物{crop_data.get_crop_name()}")
        return True, None

    def hoeing(self, row: str, line: int) -> tuple[bool, str | None]:
        data: FarmField = self.get_field(row, line)
        if data.state == 1:
            return False, f"已经锄过地了"
        if not self.endurance_change(-20):
            return False, "体力不足"
        data.hoeing()
        log.debug(f"{self.id}在{row}{line}位置锄地")
        return True, None

    def fertilization(self, row: str, line: int, fertilizer: str, user_data: UserData) -> tuple[bool, str | None]:
        data: FarmField = self.get_field(row, line)
        if data.state == 0:
            return False, f"请先锄地"
        if data.crop != "" or data.state == 0:
            return False, f"已经种植作物不能再施肥了"
        if not fertilizer in fertilizer_data_list:
            return False, f"{fertilizer} 的肥料数据未找到"
        if not user_data.item_num_change(fertilizer, -1):
            return False, f"背包中没有 {fertilizer}"
        data.fertilization(fertilizer)
        log.debug(f"{self.id}在{row}{line}位置使用{fertilizer}")
        return True, None

    def c_remove(self, row, line):
        data: FarmField = self.get_field(row, line)
        if data.state == 0:
            return False, f"请先锄地"
        if data.crop is None:
            return False, f"没有作物"
        if not self.endurance_change(-20):
            return False, "体力不足"
        crop = data.crop
        data.c_remove()
        log.debug(f"{self.id}铲除{row}{line}位置的作物{crop}")
        return True, None

    def update(self, user_date, weather):
        day = datetime.strptime(user_date, '%Y-%m-%d').date()
        if day == date.today():
            return
        self.date = date.today()
        self.endurance = 1500
        self.lucky = randint(-100, 100)
        user_table.update(f"DATE = '{date.today()}', ENDURANCE = {self.endurance}, LUCKY = {self.lucky}",
                          f"ID = {self.id}")
        is_cross_seasonal = False
        if Month(date.today().month).to_season() != Month(day.month).to_season():
            is_cross_seasonal = True
        for i in range(len(self.fields)):
            if not self.fields[i].is_out_season(is_cross_seasonal):
                rainy = False
                if 1 <= weather <= 3:
                    rainy = True
                self.fields[i].water_change(rainy)
        self.save_user_data()
        log.debug(f"为{self.id}更新农场数据")

    def to_dict(self) -> dict:
        data = {
            'ID': self.id,
            'DATE': self.date,
            'ENDURANCE': self.endurance,
            'LUCKY': self.lucky,
            'EXP': self.exp,
        }
        for i, field in enumerate(self.fields):
            data[f"FIELD_{chr(ord('A') + int(i / 8))}{i % 8 + 1}"] = json.dumps(field.to_field_dic(),
                                                                                ensure_ascii=False)
        return data

    def save_user_data(self):
        user_farm_datas.save_user_data(self)

    def _check_fields_modify(self) -> bool:
        for field in self.fields:
            if field.is_modify():
                return True
        return False

    def is_modify(self) -> bool:
        return self._modify or self._check_fields_modify()

    def __setattr__(self, key, value):
        if not key.startswith('_'):
            self._modify = True
        super().__setattr__(key, value)

    def __enter__(self):
        user_farm_datas.user_lock[self.id].acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None and self.is_modify():
            log.debug(f'保存用户{self.id}的农场信息')
            user_farm_datas.save_user_data(self)
        user_farm_datas.user_lock[self.id].release()
        return False


class UserFarmDataManager:
    def __init__(self):
        self.user_lock = GroupLock()
        self._user_list = []
        content = user_table.select_all("ID")
        for _id in content:
            self._user_list.append(str(_id[0]))

        def update_farm():
            _today_weather = self.weather
            self.weather = self.next_weather
            _next_day = date.today() + timedelta(days=1)
            self.next_weather = get_weather(_next_day.month, _next_day.day, _today_weather)
            farm_table.update(f"DATE = '{date.today()}', WEATHER = {self.weather}, NEXT_WEATHER = {self.next_weather}",
                              f"DATE != '{date.today()}'")

        content = farm_table.select_all()
        if len(content) <= 0:
            self.weather = 0
            next_day = date.today() + timedelta(days=1)
            self.next_weather = get_weather(next_day.month, next_day.day, 0)
            farm_table.insert("DATE, WEATHER, NEXT_WEATHER", f"'{date.today()}', {self.weather}, {self.next_weather}")
        else:
            day = datetime.strptime(content[0][0], '%Y-%m-%d').date()
            today = date.today()
            if day != today:
                if (day - today).days == 1:
                    yesterday_weather = self.weather
                    self.weather = self.next_weather
                    self.next_weather = get_weather(today.month, today.day, yesterday_weather)
                else:
                    last_day = today - timedelta(days=1)
                    self.weather = get_weather(last_day.month, last_day.day, choices([0, 1], [0.8, 0.2])[0])
                    self.next_weather = get_weather(today.month, today.day, self.weather)
                update_farm()
            else:
                self.weather = content[0][1]
                self.next_weather = content[0][2]

    def has_user(self, user_id) -> bool:
        user_id = str(user_id)
        if user_id in self._user_list:
            return True
        self.new_farm_user(user_id)
        return False

    def new_farm_user(self, user_id) -> bool:
        user_id = str(user_id)
        if user_id in self._user_list:
            return False
        else:
            self._user_list.append(user_id)
        user_table.insert({"ID": user_id, "DATE": date.today()})
        log.debug(f'为用户{user_id}新建农场')
        return True

    def get_farm_data(self, user_id) -> UserFarmData:
        user_id = str(user_id)
        self.has_user(user_id)
        data = user_table.select("*", f"ID = {user_id}")
        user_date = data[0][1]
        farm_data = UserFarmData(data[0])
        farm_data.update(user_date, self.weather)
        return farm_data

    def endurance_change(self, user_id, num) -> bool:
        with self.get_farm_data(user_id) as user_data:
            result = user_data.endurance_change(num)
        return result

    def lucky_change(self, user_id, num):
        with self.get_farm_data(user_id) as user_data:
            user_data.lucky_change(num)

    def exp_change(self, user_id, num) -> bool:
        with self.get_farm_data(user_id) as user_data:
            result = user_data.exp_change(num)
        return result

    @staticmethod
    def save_user_data(user_data: UserFarmData):
        user_table.update(user_data.to_dict(), f"ID = {user_data.id}")


user_farm_datas = UserFarmDataManager()


def get_user_farm_data(user_id) -> UserFarmData:
    return user_farm_datas.get_farm_data(user_id)
