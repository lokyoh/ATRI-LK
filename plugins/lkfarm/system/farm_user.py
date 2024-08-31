import json
from copy import deepcopy
from datetime import date, datetime, timedelta
from random import randint

from ATRI.log import log
from ATRI.utils.limiter import LimitedQueue
from ATRI.utils.lock import SingleLock
from ATRI.utils.sqlite import DataBase
from ATRI.system.lkbot.data.user import lk_db, users
from ATRI.system.lkbot.tools.daily_update import daily_update_event

from .crop import crop_data_list, CropData, Month
from .weather import get_weather


class FarmField:
    def __init__(self, data):
        # 0未耕种 1已耕种
        self.state = data.get("state", 0)
        # 0未浇水 1已浇水
        self.water = data.get("water", 0)
        self.water_date = data.get("water_date", "")
        self.crop = data.get("crop", "")
        self.days = data.get("days", 0)
        self.harvest = data.get("harvest", False)
        self.fertilizer = data.get("fertilizer", "")
        self.fertilization_date = data.get("fertilization_date", "")

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
                    return True, json.dumps(self.to_field_dic(), ensure_ascii=False)
        return False, None

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
        return json.dumps(self.to_field_dic(), ensure_ascii=False)


class UserFarmData:
    def __init__(self, data):
        self.endurance = data[2]
        self.lucky = data[3]
        self.exp = data[4]
        self.field = []
        for i in range(5, len(data), 1):
            self.field.append(FarmField(json.loads(data[i])))

    def get_field(self, row, line) -> FarmField:
        return self.field[(ord(row) - ord('A')) * 8 + line - 1]


class UserFarmDataManager:
    _lock = SingleLock()
    _cache_lock = SingleLock()

    def __init__(self):
        self._user_list = []
        self._cache_list = LimitedQueue(8)
        self._farm_cache = {}

        def update_user_db(connection, version):
            cursor = connection.cursor()
            if version < 1:
                pass
            cursor.close()

        self._user_db = lk_db.get_table("LKFARMUSERDATA", '''
        ID          INTEGER PRIMARY KEY,
        DATE        TEXT    DEFAULT '2000-01-01',
        ENDURANCE   INTEGER DEFAULT 1000,
        LUCKY       INTEGER DEFAULT 50,
        EXP         INTEGER DEFAULT 0,
        FIELD_A1    TEXT    DEFAULT '{}',
        FIELD_A2    TEXT    DEFAULT '{}',
        FIELD_A3    TEXT    DEFAULT '{}',
        FIELD_A4    TEXT    DEFAULT '{}',
        FIELD_A5    TEXT    DEFAULT '{}',
        FIELD_A6    TEXT    DEFAULT '{}',
        FIELD_A7    TEXT    DEFAULT '{}',
        FIELD_A8    TEXT    DEFAULT '{}',
        FIELD_B1    TEXT    DEFAULT '{}',
        FIELD_B2    TEXT    DEFAULT '{}',
        FIELD_B3    TEXT    DEFAULT '{}',
        FIELD_B4    TEXT    DEFAULT '{}',
        FIELD_B5    TEXT    DEFAULT '{}',
        FIELD_B6    TEXT    DEFAULT '{}',
        FIELD_B7    TEXT    DEFAULT '{}',
        FIELD_B8    TEXT    DEFAULT '{}',
        FIELD_C1    TEXT    DEFAULT '{}',
        FIELD_C2    TEXT    DEFAULT '{}',
        FIELD_C3    TEXT    DEFAULT '{}',
        FIELD_C4    TEXT    DEFAULT '{}',
        FIELD_C5    TEXT    DEFAULT '{}',
        FIELD_C6    TEXT    DEFAULT '{}',
        FIELD_C7    TEXT    DEFAULT '{}',
        FIELD_C8    TEXT    DEFAULT '{}',
        FIELD_D1    TEXT    DEFAULT '{}',
        FIELD_D2    TEXT    DEFAULT '{}',
        FIELD_D3    TEXT    DEFAULT '{}',
        FIELD_D4    TEXT    DEFAULT '{}',
        FIELD_D5    TEXT    DEFAULT '{}',
        FIELD_D6    TEXT    DEFAULT '{}',
        FIELD_D7    TEXT    DEFAULT '{}',
        FIELD_D8    TEXT    DEFAULT '{}'
        ''', 0, update_user_db)

        def update_db(connection, version):
            cursor = connection.cursor()
            if version < 1:
                pass
            cursor.close()

        self._db = lk_db.get_table("LKFARM", '''
        DATE            TEXT    DEFAULT '2000-1-1',
        WEATHER         INTEGER DEFAULT 0,
        NEXT_WEATHER    INTEGER DEFAULT 0
        ''', 0, update_db)
        content = self._user_db.select_all("ID")
        for _id in content:
            self._user_list.append(str(_id[0]))

        @self._cache_lock.run
        def update_farm():
            _today_weather = self.weather
            self.weather = self.next_weather
            _next_day = date.today() + timedelta(days=1)
            self.next_weather = get_weather(_next_day.month, _next_day.day, _today_weather)
            self._db.update(f"DATE = '{date.today()}', WEATHER = {self.weather}, NEXT_WEATHER = {self.next_weather}",
                            "'rowid' = 1")
            self._cache_list = LimitedQueue(8)
            self._farm_cache = {}

        content = self._db.select_all()
        if len(content) <= 0:
            self.weather = 0
            next_day = date.today() + timedelta(days=1)
            self.next_weather = get_weather(next_day.month, next_day.day, 0)
            self._db.insert("DATE, WEATHER, NEXT_WEATHER", f"'{date.today()}', {self.weather}, {self.next_weather}")
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
                    self.weather = get_weather(last_day.month, last_day.day, 0)
                    self.next_weather = get_weather(today.month, today.day, self.weather)
                update_farm()
            else:
                self.weather = content[0][1]
                self.next_weather = content[0][2]

        @daily_update_event.handle()
        def _():
            log.info("开始更新农场数据")
            db = DataBase("lkbot.db")

            @self._cache_lock.run
            def daily_up_date():
                _today_weather = self.weather
                self.weather = self.next_weather
                _next_day = date.today() + timedelta(days=1)
                self.next_weather = get_weather(_next_day.month, _next_day.day, _today_weather)
                db.get_exist_table("LKFARM").update(
                    f"DATE = '{date.today()}', WEATHER = {self.weather}, NEXT_WEATHER = {self.next_weather}",
                    "'rowid' = 1")
                self._cache_list = LimitedQueue(8)
                self._farm_cache = {}

            daily_up_date()
            db.disconnect()
            log.success("农场数据更新成功")

    def has_user(self, user_id) -> bool:
        if user_id in self._user_list:
            return True
        return False

    @_cache_lock.run
    def new_farm_user(self, user_id) -> bool:

        @self._lock.run
        def _new_farm_user():
            if self.has_user(user_id):
                return False
            self._user_list.append(user_id)
            return True

        if not _new_farm_user():
            return False
        self._user_db.insert("ID", user_id)
        return True

    def _init_farm_data(self, user_id):
        if not user_id in self._farm_cache:
            old_user = self._cache_list.add(user_id)
            if old_user:
                del self._farm_cache[old_user]
            data = self._user_db.select("*", f"ID = {user_id}")
            day = datetime.strptime(data[0][1], '%Y-%m-%d').date()
            farm_date = UserFarmData(data[0])
            if day != date.today():
                farm_date.endurance = 1000
                farm_date.lucky = 50
                self._user_db.update(
                    f"DATE = '{date.today()}', ENDURANCE = {farm_date.endurance}, LUCKY = {farm_date.lucky}",
                    f"ID = {user_id}")
                for i in range(len(farm_date.field)):
                    out_season, sqlstr = farm_date.field[i].is_out_season()
                    if out_season:
                        self._field_change(user_id, chr(int(i / 8) + ord('A')), i % 8 + 1, sqlstr)
                    else:
                        rainy = False
                        if self.weather == 1 or self.weather == 2:
                            rainy = True
                        sqlstr = farm_date.field[i].water_change(rainy)
                        if sqlstr:
                            self._field_change(user_id, chr(int(i / 8) + ord('A')), i % 8 + 1, sqlstr)
            self._farm_cache[user_id] = farm_date

    @_cache_lock.run
    def get_farm_data(self, user_id) -> UserFarmData:
        self._init_farm_data(user_id)
        return deepcopy(self._farm_cache[user_id])

    @_cache_lock.run
    def endurance_change(self, user_id, num) -> bool:
        self._init_farm_data(user_id)
        result = self._endurance_change(user_id, num)
        return result

    def _endurance_change(self, user_id, num) -> bool:
        num = self._farm_cache[user_id].endurance + num
        if num < 0:
            return False
        self._farm_cache[user_id].endurance = num
        self._user_db.update(f"ENDURANCE = {num}", f"ID = {user_id}")
        return True

    @_cache_lock.run
    def lucky_change(self, user_id, num) -> bool:
        self._init_farm_data(user_id)
        num = self._farm_cache[user_id].lucky + num
        if num < 0:
            return False
        self._farm_cache[user_id].lucky = num
        self._user_db.update(f"LUCKY ={num}", f"ID = {user_id}")
        return True

    @_cache_lock.run
    def exp_change(self, user_id, num) -> bool:
        self._init_farm_data(user_id)
        result = self._exp_change(user_id, num)
        return result

    def _exp_change(self, user_id, num) -> bool:
        num = self._farm_cache[user_id].exp + num
        if num < 0:
            return False
        self._farm_cache[user_id].exp = num
        self._user_db.update(f"EXP = {num}", f"ID = {user_id}")
        return True

    def _field_change(self, user_id, row: str, line: int, data: str):
        self._user_db.update(f"FIELD_{row}{line} = '{data}'", f"ID = {user_id}")

    @_cache_lock.run
    def seeding(self, user_id, row: str, line: int, crop: str):
        """请检查种子是否存在"""
        self._init_farm_data(user_id)
        data: FarmField = self._farm_cache[user_id].get_field(row, line)
        if data.state == 0:
            return False, f"{row}{line} 请先锄地"
        if data.crop != "" or data.state == 0:
            return False, f"{row}{line} 已经有作物了"
        if not crop in crop_data_list:
            return False, f"{crop} 的作物数据未找到"
        month = date.today().month
        if not crop_data_list[crop].growable(month):
            return False, f"{crop} 不能在{Month(month).to_season().value()}播种"
        if not users.item_num_change(user_id, f"{crop}种子", -1):
            return False, f"背包中没有 {crop}种子"
        data.crop = crop
        data.days = 0
        data.harvest = False
        self._field_change(user_id, row, line, json.dumps(data.to_field_dic(), ensure_ascii=False))
        return True, None

    @_cache_lock.run
    def watering(self, user_id, row: str, line: int):
        self._init_farm_data(user_id)
        data: FarmField = self._farm_cache[user_id].get_field(row, line)
        if data.state == 0:
            return False, f"{row}{line} 请先锄地"
        if data.water == 1:
            return False, f"{row}{line} 已经浇水了"
        if not self._endurance_change(user_id, -20):
            return False, "体力不足"
        data.water = 1
        data.water_date = str(date.today())
        self._field_change(user_id, row, line, json.dumps(data.to_field_dic(), ensure_ascii=False))
        return True, None

    @_cache_lock.run
    def harvesting(self, user_id, row: str, line: int):
        self._init_farm_data(user_id)
        data: FarmField = self._farm_cache[user_id].get_field(row, line)
        if data.state == 0:
            return False, f"{row}{line} 请先锄地"
        if data.crop == "":
            return False, f"{row}{line} 没有作物"
        if not data.crop in crop_data_list:
            return False, f"{row}{line} 未知作物"
        crop_data: CropData = crop_data_list[data.crop]
        if not crop_data.can_harvest(data.days, data.harvest):
            return False, f"{row}{line} 不可收获"
        if crop_data.is_lasting():
            data.harvest = True
        else:
            data.crop = ""
            data.harvest = False
        data.days = 0
        self._exp_change(user_id, crop_data.get_harvest_exp())
        self._field_change(user_id, row, line, json.dumps(data.to_field_dic(), ensure_ascii=False))
        harvest_list = crop_data.get_harvest_list()
        for item in harvest_list.keys():
            users.item_num_change(user_id, item, harvest_list[item])
        return True, None

    @_cache_lock.run
    def hoeing(self, user_id, row: str, line: int):
        self._init_farm_data(user_id)
        data: FarmField = self._farm_cache[user_id].get_field(row, line)
        if data.state == 1:
            return False, f"{row}{line} 已经锄过地了"
        if not self._endurance_change(user_id, -20):
            return False, "体力不足"
        data.state = 1
        self._field_change(user_id, row, line, json.dumps(data.to_field_dic(), ensure_ascii=False))
        return True, None


user_farm_data = UserFarmDataManager()
