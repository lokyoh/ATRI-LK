from datetime import datetime
import json

from ATRI.utils.event import Event
from ATRI.utils.curve import LvlManager
from ATRI.database.sqlite import MySQLite

DB_NAME = "lkbot.db"

user_level_manager = LvlManager(10, 1.5)


class Users:
    user_name_changed_event = Event()

    def __init__(self):
        self.userdata = dict()
        self.name = list()
        self.sql = MySQLite(DB_NAME, "USERINFO")
        self.sql.create_table('''
ID          INTEGER PRIMARY KEY,
NAME        TEXT NOT NULL,
EXP         INTEGER DEFAULT 0,
MONEY       INTEGER DEFAULT 0,
LASTSIGN    TEXT DEFAULT '2000-01-01',
SIGNDAYS    INTEGER DEFAULT 0,
BACKPACK    TEXT DEFAULT '{}',
PETNAME     TEXT DEFAULT '',
LOVE        INTEGER DEFAULT 0,
DATA        TEXT DEFAULT '{}'
'''
                              )
        content = self.sql.read_all()
        for row in content:
            self.name.append(row[1])
            self.userdata[str(row[0])] = UserData(row, 0)

    def add(self, user_id, name):
        self.name.append(name)
        user_data = UserData([user_id, name], 1)
        self.userdata[user_id] = user_data
        self.sql.insert('ID, NAME', f"{user_id}, '{name}'")

    def change_name(self, user_id, name, new_name):
        self.name.remove(name)
        self.name.append(new_name)
        self.userdata[user_id].name = new_name
        self.sql.update(f"NAME = '{new_name}'", f"ID = {user_id}")
        self.user_name_changed_event.notify(user_id, new_name)

    def sign(self, user_id):
        today = datetime.now().strftime("%Y-%m-%d")
        if self.userdata[user_id].lastsign == today:
            return False
        else:
            self.userdata[user_id].lastsign = today
            self.userdata[user_id].signdays += 1
            self.sql.update(f"LASTSIGN = '{today}', SIGNDAYS = {self.userdata[user_id].signdays}", f"ID={user_id}")
            self.exp_change(user_id, 3)
            self.money_change(user_id, 10)
            self.love_change(user_id, 1)
            return True

    def exp_change(self, user_id, num: int):
        if num > 0:
            self.userdata[user_id].add_exp(num)
            self.sql.update(f"EXP = '{self.userdata[user_id].exp}'", f"ID = {user_id}")
            return True
        return False

    def money_change(self, user_id, num: int) -> bool:
        if num != 0:
            money = self.userdata[user_id].money
            money += num
            if money >= 0:
                self.userdata[user_id].money = money
                self.sql.update(f"MONEY = '{money}'", f"ID = {user_id}")
                return True
        return False

    def love_change(self, user_id, num):
        if num > 0:
            love = self.userdata[user_id].love
            love += num
            self.userdata[user_id].love = love
            self.sql.update(f"LOVE = '{love}'", f"ID = {user_id}")
            return True
        return False


class UserData:
    def __init__(self, data, data_type):
        if data_type == 0:
            self.id = data[0]
            self.name = data[1]
            self.exp = data[2]
            self.money = data[3]
            self.lastsign = data[4]
            self.signdays = data[5]
            self.backpack = json.loads(data[6])
            self.petname = data[7]
            self.love = data[8]
            self.data = json.loads(data[9])
            self.lvl = user_level_manager.to_lvl(self.exp)
            self.left_exp = user_level_manager.get_left_exp(self.exp, self.lvl)
            self.extra = {}
        else:
            self.id = data[0]
            self.name = data[1]
            self.exp = 0
            self.money = 0
            self.lastsign = '2000-01-01'
            self.signdays = 0
            self.backpack = {}
            self.petname = ''
            self.love = 0
            self.data = {}
            self.lvl = 0
            self.left_exp = user_level_manager.get_left_exp(self.exp, self.lvl)
            self.extra = {}

    def get_lvl_exp(self):
        return user_level_manager.get_lvl_exp(self.lvl)

    def add_exp(self, exp: int):
        self.exp += exp
        self.left_exp += exp
        lvl_exp = self.get_lvl_exp()
        if self.left_exp >= lvl_exp:
            self.left_exp -= lvl_exp
            self.lvl += 1


users = Users()
