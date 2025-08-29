import atexit
from datetime import datetime
import json

from ATRI.log import log
from ATRI.utils.event import DictEvent
from ATRI.utils.curve import LvlManager
from ATRI.utils.sqlite import DataBase
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
            self.backpack = BackPack(json.loads(data[6]))
            self.love = data[7]
            self.exp_mul = data[8]
            self.exp_mul_count = data[9]
            self.love_mul = data[10]
            self.love_mul_count = data[11]
        else:
            self.exp = 0
            self.money = 0
            self.lastsign = '2000-01-01'
            self.signdays = 0
            self.backpack = BackPack({})
            self.love = 0
            self.exp_mul = 100
            self.exp_mul_count = 0
            self.love_mul = 100
            self.love_mul_count = 0
        self.lvl = user_level_manager.to_lvl(self.exp)
        self.left_exp = user_level_manager.get_left_exp(self.exp, self.lvl)
        self.extra = {}
        self._modify = False

    def get_lvl_exp(self):
        """获取当前等级的经验总值"""
        return user_level_manager.get_lvl_exp(self.lvl)

    def exp_change(self, exp: int, mult: bool) -> bool:
        """增减经验"""
        if exp <= 0:
            return False
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
        return True

    def love_change(self, num: int, mult: bool) -> bool:
        """增减经验好感值"""
        if num <= 0:
            return False
        if mult:
            num = int(num * lk_data.love_mul / 100)
            if self.love_mul_count > 0:
                self.love_mul_count -= 1
                num = int(num * self.love_mul / 100)
        self.love += num
        return True

    def money_change(self, num: int) -> bool:
        """增减ATRI币"""
        if num != 0:
            money = self.money
            money += num
            if money >= 0:
                self.money = money
                return True
            return False
        return False

    def item_num_change(self, item_name: str, num: int) -> bool:
        """修改指定用户指定物品的数量"""
        item_stack = self.backpack.get_item_stack(item_name)
        num += item_stack.meta.num
        if num < 0:
            return False
        item_stack.meta.num = num
        self.backpack.set_item_with_stack(item_stack)
        return True

    def exp_mul_change(self, exp_mul: int, times: int):
        if self.exp_mul != 100 and self.love_mul != exp_mul:
            if self.exp_mul_count != 0:
                return False
        self.exp_mul = exp_mul
        self.exp_mul_count += times
        return True

    def love_mul_change(self, love_mul: int, times: int):
        if self.love_mul != 100 and self.love_mul != love_mul:
            if self.love_mul_count != 0:
                return False
        self.love_mul = love_mul
        self.love_mul_count += times
        return True

    def save_user_data(self):
        """保存用户数据"""
        users.save_user_data(self)

    def __setattr__(self, key, value):
        if not key.startswith('_'):
            self._modify = True
        super().__setattr__(key, value)

    def __enter__(self):
        users.user_lock[self.id].acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None and (self._modify or self.backpack.is_modify()):
            self.save_user_data()
        users.user_lock[self.id].release()
        return False


class UserNameChangedEvent(DictEvent):
    """用户名改变事件"""

    def notify(self, user_id: str, user_name: str):
        super().notify(user_id, user_name)


class Users:
    """
    用户信息管理器
    """
    user_name_changed_event = UserNameChangedEvent("user_name_changed")
    """用户名改变事件"""
    _name_lock = SingleLock()

    def __init__(self):
        self.user_lock = GroupLock()
        self._ids = {}
        self._names = []
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
        content = self.sql.select_all("ID, NAME")
        for row in content:
            self._ids[str(row[0])] = str(row[1])
            self._names.append(row[1])
            self.user_lock.add_lock(str(row[0]))

    def get_name_list(self) -> tuple:
        """获取用户名表"""
        return tuple(self._names)

    def get_id_list(self) -> tuple:
        """获取用户ID表"""
        return tuple(self._ids.keys())

    def get_user_data(self, user_id: str) -> UserData:
        """返回用户数据"""
        return self.load_user_data(user_id)

    def add_user(self, user_id: str, name: str) -> bool:
        """添加用户"""
        if not self.add_name_list(user_id, name):
            return False
        self.user_lock.add_lock(user_id)

        @self.user_lock.lock(user_id)
        def _add_user():
            self.add_user_data(user_id, name)

        _add_user()
        return True

    @_name_lock.run
    def add_name_list(self, uid, name):
        if name in self._names:
            return False
        self._names.append(name)
        self._ids[uid] = name
        return True

    def change_name(self, user_id: str, name: str, new_name: str) -> bool:
        """修改用户名"""
        if not self.change_name_list(name, new_name):
            return False

        @self.user_lock.lock(user_id)
        def _change_name():
            user_data = self.get_user_data(user_id)
            user_data.name = new_name
            user_data.save_user_data()
            self.user_name_changed_event.notify(user_id, new_name)

        _change_name()
        return True

    @_name_lock.run
    def change_name_list(self, name, new_name):
        if new_name in self._names:
            return False
        self._names.remove(name)
        self._names.append(new_name)
        return True

    def sign(self, user_id: str) -> tuple[bool, str]:
        """签到"""
        with self.get_user_data(user_id) as user_data:
            r = self.sign_func(user_data)
        return r

    @staticmethod
    def sign_func(user_data: UserData) -> tuple[bool, str]:
        """签到"""
        today = datetime.now().strftime("%Y-%m-%d")
        if user_data.lastsign == today:
            return False, "今日已签到"
        else:
            from ..util import sign_in_event
            user_data.lastsign = today
            user_data.signdays += 1
            user_data.exp_change(3, True)
            user_data.money_change(10)
            user_data.love_change(1, True)
            msg = f'{sign_in_event.notify(user_data)}'
            return True, msg

    def get_user_name(self, user_id: str) -> str:
        """获取用户名，用户id错误会报错，lk_util.get_name为包装后的方法"""
        return self._ids[user_id]

    def has_user(self, user_id: str) -> bool:
        """是否拥有用户,与lk_util.is_valid_user一致"""
        if user_id in self._ids:
            return True
        return False

    def load_user_data(self, user_id) -> UserData:
        content = self.sql.select(
            "ID, NAME, EXP, MONEY, LASTSIGN, SIGNDAYS, BACKPACK, LOVE, EXPMUL, EXPMULCOUNT, LOVEMUL, LOVEMULCOUNT",
            f"ID = {user_id}")
        return UserData(content[0], 0)

    def save_user_data(self, user_data: UserData):
        self.sql.update(((
                             'NAME',
                             'EXP',
                             'MONEY',
                             'LASTSIGN',
                             'SIGNDAYS',
                             'BACKPACK',
                             'LOVE',
                             'EXPMUL',
                             'EXPMULCOUNT',
                             'LOVEMUL',
                             'LOVEMULCOUNT'
                         ), (
                             user_data.name,
                             user_data.exp,
                             user_data.money,
                             user_data.lastsign,
                             user_data.signdays,
                             user_data.backpack.bp_to_str(),
                             user_data.love,
                             user_data.exp_mul,
                             user_data.exp_mul_count,
                             user_data.love_mul,
                             user_data.love_mul_count,
                         )), f"ID = {user_data.id}")

    def add_user_data(self, user_id, name):
        self.sql.insert('ID, NAME', (int(user_id), name))


lk_db = DataBase("lkbot.db")
"""插件专用数据库"""
atexit.register(lk_db.disconnect)
users = Users()
"""管理用户数据"""
