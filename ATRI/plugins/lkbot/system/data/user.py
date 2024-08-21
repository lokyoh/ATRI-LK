import copy
from copy import deepcopy
from datetime import datetime
import json
from sqlite3 import Connection

from ATRI.utils.event import Event
from ATRI.utils.curve import LvlManager
from ATRI.utils.sqlite import DataBase
from ATRI.utils.limiter import LimitedQueue
from ATRI.utils.lock import SingleLock, GroupLock

from .item import BackPack

user_level_manager = LvlManager(10, 1.5)


class UserData:
    """
    用户数据类:
    id: str 用户ID
    name: str 用户名称
    exp: int 用户经验总值
    money: int 用户ATRI币
    lastsign: str 用户上次登录日期
    signdays: int 用户签到总日期
    love: int 用户好感值
    lvl: int 用户等级
    left_exp: int 用户升级剩余经验
    petname: str 用户宠物名
    extra: dict 供其他插件使用的额外信息
    """

    def __init__(self, data, data_type):
        if data_type == 0:
            self.id = data[0]
            self.name = data[1]
            self.exp = data[2]
            self.money = data[3]
            self.lastsign = data[4]
            self.signdays = data[5]
            self.love = data[6]
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
            self.love = 0
            self.lvl = 0
            self.left_exp = user_level_manager.get_left_exp(self.exp, self.lvl)
            self.petname = ''
            self.extra = {}

    def get_lvl_exp(self):
        """获取当前等级的经验总值"""
        return user_level_manager.get_lvl_exp(self.lvl)

    def add_exp(self, exp: int):
        """建议用users.exp_change()代替"""
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
    """
    使用users来使用其中的方法，from ATRI.plugins.lkbot.system.data.user import users
    """
    user_name_changed_event = UserNameChangedEvent()
    _lock = SingleLock()
    _name_lock = SingleLock()
    _user_lock = GroupLock()

    def __init__(self):
        self._userdata = dict()
        self._name = list()
        self._backpack_list = LimitedQueue(8)
        self._backpack_cache = {}
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
        content = self.sql.select_all("ID, NAME, EXP, MONEY, LASTSIGN, SIGNDAYS, LOVE")
        for row in content:
            self._name.append(row[1])
            self._userdata[str(row[0])] = UserData(row, 0)
            self._user_lock.add_lock(str(row[0]))

    def get_name_list(self) -> list:
        """获取用户名表"""
        return copy.deepcopy(self._name)

    def get_id_list(self) -> list:
        """获取用户ID表"""
        return list(self._userdata.keys())

    def get_user_data(self, user_id: str) -> UserData:
        """返回一个深度拷贝，不要用于修改数据"""
        return copy.deepcopy(self._userdata[user_id])

    def add_user(self, user_id: str, name: str) -> bool:
        """添加用户"""
        if not self.add_name_list(name):
            return False
        self._user_lock.add_lock(user_id)

        def _add_user():
            self._userdata[user_id] = UserData([user_id, name], 1)
            self.sql.insert('ID, NAME', f"{user_id}, '{name}'")

        self._user_lock.run(_add_user, user_id)()
        return True

    @_name_lock.run
    def add_name_list(self, name):
        if name in self._name:
            return False
        self._name.append(name)
        return True

    def change_name(self, user_id: str, name: str, new_name: str) -> bool:
        """修改用户名"""
        if not self.change_name_list(name, new_name):
            return False

        def _change_name():
            self._userdata[user_id].name = new_name
            self.sql.update(f"NAME = '{new_name}'", f"ID = {user_id}")

        self._user_lock.run(_change_name, user_id)()
        self.user_name_changed_event.notify(user_id, new_name)
        return True

    @_name_lock.run
    def change_name_list(self, name, new_name):
        if new_name in self._name:
            return False
        self._name.remove(name)
        self._name.append(new_name)
        return True

    def sign(self, user_id: str) -> bool:
        """签到"""
        today = datetime.now().strftime("%Y-%m-%d")
        if self._userdata[user_id].lastsign == today:
            return False
        else:
            def _sign():
                self._userdata[user_id].lastsign = today
                self._userdata[user_id].signdays += 1
                self.sql.update(f"LASTSIGN = '{today}', SIGNDAYS = {self._userdata[user_id].signdays}", f"ID={user_id}")
                self._exp_change(user_id, 3)
                self._money_change(user_id, 10)
                self._love_change(user_id, 1)

            self._user_lock.run(_sign, user_id)()
            return True

    def exp_change(self, user_id: str, num: int) -> bool:
        """增减经验"""
        if num > 0:
            self._user_lock.run(self.exp_change, user_id)(user_id, num)
            return True
        return False

    def _exp_change(self, user_id: str, num: int):
        self._userdata[user_id].add_exp(num)
        self.sql.update(f"EXP = '{self._userdata[user_id].exp}'", f"ID = {user_id}")

    def money_change(self, user_id: str, num: int) -> bool:
        """增减ATRI币"""
        if num != 0:
            return self._user_lock.run(self._money_change, user_id)(user_id, num)
        return False

    def _money_change(self, user_id: str, num: int):
        money = self._userdata[user_id].money
        money += num
        if money >= 0:
            self._userdata[user_id].money = money
            self.sql.update(f"MONEY = '{money}'", f"ID = {user_id}")
            return True
        return False

    def love_change(self, user_id: str, num: int) -> bool:
        """增减好感度"""
        if num > 0:
            self._user_lock.run(self._love_change, user_id)(user_id, num)
            return True
        return False

    def _love_change(self, user_id: str, num: int):
        love = self._userdata[user_id].love
        love += num
        self._userdata[user_id].love = love
        self.sql.update(f"LOVE = '{love}'", f"ID = {user_id}")

    def petname_set(self, user_id: str, name: str):
        """设置用户的宠物名，使用对lk宠物插件无效，只改变显示名称"""
        def _petname_set():
            self._userdata[user_id].petname = name

        self._user_lock.run(_petname_set, user_id)()

    def get_user_name(self, user_id: str) -> str:
        """推荐使用lk_util.get_name，获取用户名"""
        return self._userdata[user_id].name

    def has_user(self, user_id) -> bool:
        """是否拥有用户,与lk_util.is_valid_user一致"""
        if user_id in self._userdata:
            return True
        return False

    def get_money(self, user_id) -> int:
        """获取ATRI币数量"""
        return self._userdata[user_id].money

    def get_love(self, user_id) -> int:
        """获取好感度"""
        return self._userdata[user_id].love

    def get_exp(self, user_id) -> int:
        """获取经验总值"""
        return self._userdata[user_id].exp

    def get_lvl(self, user_id) -> int:
        """获取等级"""
        return self._userdata[user_id].lvl

    def get_left_exp(self, user_id) -> int:
        """获取当前等级所需的剩余经验值"""
        return self._userdata[user_id].left_exp

    def get_lvl_exp(self, user_id) -> int:
        """获取当前等级所需的所有经验值"""
        return self._userdata[user_id].get_lvl_exp()

    def _init_backpack(self, user_id):
        """
        初始化背包的原始方法
        请用数据锁保护此方法
        """
        if not user_id in self._backpack_cache:
            old_user = self._backpack_list.add(user_id)
            if old_user:
                del self._backpack_cache[old_user]
            bp = self.sql.select("BACKPACK", f"ID = {user_id}")
            self._backpack_cache[user_id] = BackPack(json.loads(bp[0][0]))

    @_lock.run
    def get_backpack(self, user_id) -> BackPack:
        """获取用户背包的深度拷贝"""
        self._init_backpack(user_id)
        return deepcopy(self._backpack_cache[user_id])

    @_lock.run
    def item_num_change(self, user_id: str, item_name: str, num: int) -> bool:
        """修改指定用户指定物品的数量"""
        self._init_backpack(user_id)
        item_stack = self._backpack_cache[user_id].get_item_stack(item_name)
        num += item_stack.meta.num
        if num < 0:
            return False
        item_stack.meta.num = num
        self._backpack_cache[user_id].set_item_with_stack(item_stack)
        self.sql.update(f"BACKPACK = '{self._backpack_cache[user_id].bp_to_str()}'",
                        f"ID = {user_id}")
        return True


class DataBaseUpdateEvent(Event):
    def notify(self, connection: Connection, version: int):
        super().notify(connection, version)


lk_db = DataBase("lkbot.db")
users = Users()
