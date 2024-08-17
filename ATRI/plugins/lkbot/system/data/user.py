import copy
from datetime import datetime
import json

from threading import Lock

from sqlite3 import Connection

from ATRI.utils.event import Event
from ATRI.utils.curve import LvlManager
from ATRI.utils.sqlite import DataBase

from .item import BackPack

user_level_manager = LvlManager(10, 1.5)


class UserData:
    """内部类不要使用"""

    def __init__(self, data, data_type):
        if data_type == 0:
            self.id = data[0]
            self.name = data[1]
            self.exp = data[2]
            self.money = data[3]
            self.lastsign = data[4]
            self.signdays = data[5]
            self.backpack = BackPack(json.loads(data[6]))
            self.love = data[7]
            self.lvl = user_level_manager.to_lvl(self.exp)
            self.left_exp = user_level_manager.get_left_exp(self.exp, self.lvl)
            self.petname = ''
            self.extra = {}
        else:
            self.id = data[0]
            self.name = data[1]
            self.exp = 0
            self.money = 0
            self.lastsign = '2000-01-01'
            self.signdays = 0
            self.backpack = BackPack({})
            self.love = 0
            self.lvl = 0
            self.left_exp = user_level_manager.get_left_exp(self.exp, self.lvl)
            self.petname = ''
            self.extra = {}

    def get_lvl_exp(self):
        return user_level_manager.get_lvl_exp(self.lvl)

    def add_exp(self, exp: int):
        """请勿直接使用，用Users中的exp_change()代替"""
        self.exp += exp
        self.left_exp += exp
        lvl_exp = self.get_lvl_exp()
        if self.left_exp >= lvl_exp:
            self.left_exp -= lvl_exp
            self.lvl += 1


class UserNameChangedEvent(Event):
    def notify(self, user_id: str, user_name: str):
        super().notify(user_id, user_name)


class Users:
    user_name_changed_event = UserNameChangedEvent()

    def __init__(self):
        self._userdata = dict()
        self._lock = dict()
        self._name = list()
        self._name_lock = Lock()
        table_content = '''
ID          INTEGER PRIMARY KEY,
NAME        TEXT NOT NULL,
EXP         INTEGER DEFAULT 0,
MONEY       INTEGER DEFAULT 0,
LASTSIGN    TEXT DEFAULT '2000-01-01',
SIGNDAYS    INTEGER DEFAULT 0,
BACKPACK    TEXT DEFAULT '{}',
LOVE        INTEGER DEFAULT 0
'''

        def update_db(connection, version):
            cursor = connection.cursor()
            if version < 1:
                cursor.execute("ALTER TABLE `TEST` DROP COLUMN `PETNAME`")
                cursor.execute("ALTER TABLE `TEST` DROP COLUMN `DATA`")
                connection.commit()
            cursor.close()

        self.sql = lk_db.get_table("USERINFO", table_content, 0, update_db)
        content = self.sql.select_all("ID, NAME, EXP, MONEY, LASTSIGN, SIGNDAYS, BACKPACK, LOVE")
        for row in content:
            self._name.append(row[1])
            self._userdata[str(row[0])] = UserData(row, 0)
            self._lock[str(row[0])] = Lock()

    def get_name_list(self) -> list:
        return copy.deepcopy(self._name)

    def get_id_list(self) -> list:
        return list(self._userdata.keys())

    def get_user_data(self, user_id: str) -> UserData:
        """返回一个深度拷贝，不要用于修改数据"""
        return copy.deepcopy(self._userdata[user_id])

    def add_user(self, user_id: str, name: str) -> bool:
        self._name_lock.acquire()
        if name in self._name:
            self._name_lock.release()
            return False
        self._name.append(name)
        self._name_lock.release()
        self._lock[user_id] = Lock()
        self._lock[user_id].acquire()
        self._userdata[user_id] = UserData([user_id, name], 1)
        self.sql.insert('ID, NAME', f"{user_id}, '{name}'")
        self._lock[user_id].release()
        return True

    def change_name(self, user_id: str, name: str, new_name: str) -> bool:
        self._name_lock.acquire()
        if new_name in self._name:
            self._name_lock.release()
            return False
        self._name.remove(name)
        self._name.append(new_name)
        self._name_lock.release()
        self._lock[user_id].acquire()
        self._userdata[user_id].name = new_name
        self.sql.update(f"NAME = '{new_name}'", f"ID = {user_id}")
        self._lock[user_id].release()
        self.user_name_changed_event.notify(user_id, new_name)
        return True

    def sign(self, user_id: str) -> bool:
        today = datetime.now().strftime("%Y-%m-%d")
        if self._userdata[user_id].lastsign == today:
            return False
        else:
            self._lock[user_id].acquire()
            self._userdata[user_id].lastsign = today
            self._userdata[user_id].signdays += 1
            self.sql.update(f"LASTSIGN = '{today}', SIGNDAYS = {self._userdata[user_id].signdays}", f"ID={user_id}")
            self._lock[user_id].release()
            self.exp_change(user_id, 3)
            self.money_change(user_id, 10)
            self.love_change(user_id, 1)
            return True

    def exp_change(self, user_id: str, num: int) -> bool:
        if num > 0:
            self._lock[user_id].acquire()
            self._userdata[user_id].add_exp(num)
            self.sql.update(f"EXP = '{self._userdata[user_id].exp}'", f"ID = {user_id}")
            self._lock[user_id].release()
            return True
        return False

    def money_change(self, user_id: str, num: int) -> bool:
        if num != 0:
            self._lock[user_id].acquire()
            money = self._userdata[user_id].money
            money += num
            if money >= 0:
                self._userdata[user_id].money = money
                self.sql.update(f"MONEY = '{money}'", f"ID = {user_id}")
                self._lock[user_id].release()
                return True
            self._lock[user_id].release()
        return False

    def love_change(self, user_id: str, num: int) -> bool:
        if num > 0:
            self._lock[user_id].acquire()
            love = self._userdata[user_id].love
            love += num
            self._userdata[user_id].love = love
            self.sql.update(f"LOVE = '{love}'", f"ID = {user_id}")
            self._lock[user_id].release()
            return True
        return False

    def petname_set(self, user_id: str, name: str):
        self._lock[user_id].acquire()
        self._userdata[user_id].petname = name
        self._lock[user_id].release()

    def get_user_name(self, user_id: str) -> str:
        return self._userdata[user_id].name

    def has_user(self, user_id) -> bool:
        if user_id in self._userdata:
            return True
        return False

    def get_money(self, user_id) -> int:
        return self._userdata[user_id].money

    def get_love(self, user_id) -> int:
        return self._userdata[user_id].love

    def get_exp(self, user_id) -> int:
        return self._userdata[user_id].exp

    def get_lvl(self, user_id) -> int:
        return self._userdata[user_id].lvl

    def get_left_exp(self, user_id) -> int:
        return self._userdata[user_id].left_exp

    def get_lvl_exp(self, user_id) -> int:
        return self._userdata[user_id].get_lvl_exp()

    def get_backpack(self, user_id) -> BackPack:
        """返回一个深度拷贝，不要用于修改数据"""
        return copy.deepcopy(self._userdata[user_id].backpack)

    def item_num_change(self, user_id: str, item_name: str, num: int):
        self._lock[user_id].acquire()
        item_stack = self._userdata[user_id].backpack.get_item_stack(item_name)
        item_stack.meta.num += num
        self._userdata[user_id].backpack.set_item_with_stack(item_stack)
        self.sql.update(f"BACKPACK = '{json.dumps(self._userdata[user_id].backpack.bp_to_dict(), ensure_ascii=False)}'",
                        f"ID = {user_id}")
        self._lock[user_id].release()


class DataBaseUpdateEvent(Event):
    def notify(self, connection: Connection, version: int):
        super().notify(connection, version)


lk_db = DataBase("lkbot.db")
users = Users()
