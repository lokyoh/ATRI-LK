import copy
from copy import deepcopy
from datetime import datetime
import json

from ATRI.log import log
from ATRI.utils.event import BaseEvent
from ATRI.utils.curve import LvlManager
from ATRI.utils.sqlite import DataBase
from ATRI.utils.limiter import LimitedQueue
from ATRI.utils.lock import SingleLock, GroupLock

from .item import BackPack
from .system import lk_data

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
    extra: dict 供其他插件使用的额外信息
    """

    def __init__(self, data, data_type):
        self.id = data[0]
        self.name = data[1]
        if data_type == 0:
            self.exp = data[2]
            self.money = data[3]
            self.lastsign = data[4]
            self.signdays = data[5]
            self.love = data[6]
            self.exp_mul = data[7]
            self.exp_mul_count = data[8]
            self.love_mul = data[9]
            self.love_mul_count = data[10]
        else:
            self.exp = 0
            self.money = 0
            self.lastsign = '2000-01-01'
            self.signdays = 0
            self.love = 0
            self.exp_mul = 100
            self.exp_mul_count = 0
            self.love_mul = 100
            self.love_mul_count = 0
        self.lvl = user_level_manager.to_lvl(self.exp)
        self.left_exp = user_level_manager.get_left_exp(self.exp, self.lvl)
        self.extra = {}

    def get_lvl_exp(self):
        """获取当前等级的经验总值"""
        return user_level_manager.get_lvl_exp(self.lvl)

    def add_exp(self, exp: int, mult: bool):
        """建议用users.exp_change()代替"""
        if mult:
            exp = int(exp * lk_data.exp_mul / 100)
            if self.exp_mul_count > 0:
                self.exp_mul_count -= 1
                exp = int(exp * self.exp_mul / 100)
        self.exp += exp
        self.left_exp += exp
        lvl_exp = self.get_lvl_exp()
        while self.left_exp >= lvl_exp:
            self.left_exp -= lvl_exp
            self.lvl += 1
            lvl_exp = self.get_lvl_exp()

    def add_love(self, num: int, mult: bool):
        """建议用users.love_change()代替"""
        if mult:
            num = int(num * lk_data.love_mul / 100)
            if self.love_mul_count > 0:
                self.love_mul_count -= 1
                num = int(num * self.love_mul / 100)
        self.love += num


class UserNameChangedEvent(BaseEvent):
    """用户名改变事件"""

    def notify(self, user_id: str, user_name: str):
        super().notify(user_id, user_name)


class Users:
    """
    用户信息管理器
    """
    user_name_changed_event = UserNameChangedEvent()
    """用户名改变事件"""
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
LOVE        INTEGER DEFAULT 0,
EXPMUL      INTEGER DEFAULT 100,
EXPMULCOUNT INTEGER DEFAULT 0,
LOVEMUL     INTEGER DEFAULT 100,
LOVEMULCOUNT    INTEGER DEFAULT 0
'''

        def update_db(connection, version):
            cursor = connection.cursor()
            if version < 1:
                cursor.execute("ALTER TABLE `USERINFO` ADD COLUMN `EXPMUL` INTEGER DEFAULT 100")
                cursor.execute("ALTER TABLE `USERINFO` ADD COLUMN `EXPMULCOUNT` INTEGER DEFAULT 0")
                cursor.execute("ALTER TABLE `USERINFO` ADD COLUMN `LOVEMUL` INTEGER DEFAULT 100")
                cursor.execute("ALTER TABLE `USERINFO` ADD COLUMN `LOVEMULCOUNT` INTEGER DEFAULT 0")
                connection.commit()
            cursor.close()
            log.info(f"用户数据表升级完成")

        self.sql = lk_db.get_table("USERINFO", table_content, 1, update_db)
        content = self.sql.select_all(
            "ID, NAME, EXP, MONEY, LASTSIGN, SIGNDAYS, LOVE, EXPMUL, EXPMULCOUNT, LOVEMUL, LOVEMULCOUNT")
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

    def sign(self, user_id: str) -> tuple[bool, str]:
        """签到"""
        today = datetime.now().strftime("%Y-%m-%d")
        if self._userdata[user_id].lastsign == today:
            return False, "今日已签到"
        else:
            def _sign():
                from ..util import sign_in_event
                self._userdata[user_id].lastsign = today
                self._userdata[user_id].signdays += 1
                self.sql.update(f"LASTSIGN = '{today}', SIGNDAYS = {self._userdata[user_id].signdays}", f"ID={user_id}")
                self._exp_change(user_id, 3, True)
                self._money_change(user_id, 10)
                self._love_change(user_id, 1, True)
                return f'{sign_in_event.notify(user_id)}'

            return True, self._user_lock.run(_sign, user_id)()

    def exp_change(self, user_id: str, num: int, mult: bool = True) -> bool:
        """增减经验"""
        if num > 0:
            self._user_lock.run(self._exp_change, user_id)(user_id, num, mult)
            return True
        return False

    def _exp_change(self, user_id: str, num: int, mult: bool):
        self._userdata[user_id].add_exp(num, mult)
        self.sql.update(f"EXP = '{self._userdata[user_id].exp}'", f"ID = {user_id}")
        if mult:
            self.sql.update(f"EXPMULCOUNT = '{self._userdata[user_id].exp_mul_count}'", f"ID = {user_id}")

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

    def love_change(self, user_id: str, num: int, mult: bool = True) -> bool:
        """增减好感度"""
        if num > 0:
            self._user_lock.run(self._love_change, user_id)(user_id, num, mult)
            return True
        return False

    def _love_change(self, user_id: str, num: int, mult: bool):
        self._userdata[user_id].add_love(num, mult)
        self.sql.update(f"LOVE = '{self._userdata[user_id].love}'", f"ID = {user_id}")
        if mult:
            self.sql.update(f"LOVEMULCOUNT = '{self._userdata[user_id].love_mul_count}'", f"ID = {user_id}")

    def get_user_name(self, user_id: str) -> str:
        """获取用户名，用户id错误会报错，lk_util.get_name为包装后的方法"""
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

    def exp_mul_change(self, user_id: str, exp_mul: int, times: int) -> bool:
        """经验倍率改变"""
        return self._user_lock.run(self._exp_mul_change, user_id)(user_id, exp_mul, times)

    def _exp_mul_change(self, user_id: str, exp_mul: int, times: int):
        if self._userdata[user_id].exp_mul != 100 and self._userdata[user_id].love_mul != exp_mul:
            if self._userdata[user_id].exp_mul_count != 0:
                return False
        self._userdata[user_id].exp_mul = exp_mul
        self._userdata[user_id].exp_mul_count += times
        self.sql.update(f"EXPMUL = '{self._userdata[user_id].exp_mul}'", f"ID = {user_id}")
        self.sql.update(f"EXPMULCOUNT = '{self._userdata[user_id].exp_mul_count}'", f"ID = {user_id}")
        return True

    def love_mul_change(self, user_id: str, love_mul: int, times: int) -> bool:
        """好感倍率改变"""
        return self._user_lock.run(self._love_mul_change, user_id)(user_id, love_mul, times)

    def _love_mul_change(self, user_id: str, love_mul: int, times: int):
        if self._userdata[user_id].love_mul != 100 and self._userdata[user_id].love_mul != love_mul:
            if self._userdata[user_id].love_mul_count != 0:
                return False
        self._userdata[user_id].love_mul = love_mul
        self._userdata[user_id].love_mul_count += times
        self.sql.update(f"LOVEMUL = '{self._userdata[user_id].love_mul}'", f"ID = {user_id}")
        self.sql.update(f"LOVEMULCOUNT = '{self._userdata[user_id].love_mul_count}'", f"ID = {user_id}")
        return True


lk_db = DataBase("lkbot.db")
"""插件专用数据库"""
users = Users()
"""管理用户数据"""
