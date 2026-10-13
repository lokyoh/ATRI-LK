import atexit
import json
import secrets
import time
from datetime import datetime

from ATRI.log import log
from ATRI.utils.curve import LvlManager
from ATRI.utils.event import BaseEvent, BaseEvents
from ATRI.utils.lock import GroupLock, SingleLock
from ATRI.utils.sqlite import DataBase

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
            self.lastsign = "2000-01-01"
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
        log.debug(f"用户{self.id}经验变化{exp},现值{self.exp}")
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
        log.debug(f"用户{self.id}好感变化{num},现值{self.love}")
        return True

    def money_change(self, num: int) -> bool:
        """增减ATRI币"""
        if num != 0:
            money = self.money
            money += num
            if money >= 0:
                self.money = money
                log.debug(f"用户{self.id}ATRI币变化{num},现值{self.money}")
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
        log.debug(f"用户{self.id}的物品`{item_name}`数量变化{num},现值{num}")
        return True

    def exp_mul_change(self, exp_mul: int, times: int):
        if self.exp_mul != 100 and self.love_mul != exp_mul and self.exp_mul_count != 0:
            return False
        self.exp_mul = exp_mul
        self.exp_mul_count += times
        log.debug(
            f"用户{self.id}经验倍率次数变化{times},现值{self.exp_mul}*{self.exp_mul_count}"
        )
        return True

    def love_mul_change(self, love_mul: int, times: int):
        if (
            self.love_mul != 100
            and self.love_mul != love_mul
            and self.love_mul_count != 0
        ):
            return False
        self.love_mul = love_mul
        self.love_mul_count += times
        log.debug(
            f"用户{self.id}好感倍率次数变化{times},现值{self.love_mul}*{self.love_mul_count}"
        )
        return True

    def save_user_data(self):
        """保存用户数据"""
        users.save_user_data(self)

    def __setattr__(self, key, value):
        if not key.startswith("_"):
            self._modify = True
        super().__setattr__(key, value)

    def __enter__(self):
        users.user_lock[self.id].acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None and (self._modify or self.backpack.is_modify()):
            log.debug(f"保存用户{self.id}的信息")
            self.save_user_data()
        users.user_lock[self.id].release()
        return False


class UserNameChangedEvent(BaseEvent):
    """
    用户名改变事件体。
    """

    def __init__(self, user_id: str, new_name: str):
        super().__init__("用户名改变事件")
        self.user_id: str = user_id
        self.new_name: str = new_name


class Users:
    """
    用户信息管理器
    """

    user_name_changed_events = BaseEvents()
    """用户名改变事件"""
    _name_lock = SingleLock()

    def __init__(self):
        self.user_lock = GroupLock()
        self._ids = {}
        self._names = []
        table_content = """
ID          TEXT PRIMARY KEY,
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
"""

        def update_db(connection, version):
            cursor = connection.cursor()
            if version < 1:
                cursor.execute(
                    "ALTER TABLE `USERINFO` ADD COLUMN `EXPMUL` INTEGER DEFAULT 100"
                )
                cursor.execute(
                    "ALTER TABLE `USERINFO` ADD COLUMN `EXPMULCOUNT` INTEGER DEFAULT 0"
                )
                cursor.execute(
                    "ALTER TABLE `USERINFO` ADD COLUMN `LOVEMUL` INTEGER DEFAULT 100"
                )
                cursor.execute(
                    "ALTER TABLE `USERINFO` ADD COLUMN `LOVEMULCOUNT` INTEGER DEFAULT 0"
                )
                connection.commit()
            if version < 2:
                cursor.execute("DROP TABLE IF EXISTS `USERINFO_NEW`")
                cursor.execute(f"CREATE TABLE `USERINFO_NEW` ({table_content})")
                cursor.execute(
                    """
                    INSERT INTO `USERINFO_NEW` (
                        ID, NAME, EXP, MONEY, LASTSIGN, SIGNDAYS, BACKPACK,
                        LOVE, EXPMUL, EXPMULCOUNT, LOVEMUL, LOVEMULCOUNT
                    )
                    SELECT CAST(ID AS TEXT), NAME, EXP, MONEY, LASTSIGN, SIGNDAYS,
                           BACKPACK, LOVE, EXPMUL, EXPMULCOUNT, LOVEMUL, LOVEMULCOUNT
                    FROM `USERINFO`
                    """
                )
                cursor.execute("DROP TABLE `USERINFO`")
                cursor.execute("ALTER TABLE `USERINFO_NEW` RENAME TO `USERINFO`")
            cursor.close()
            connection.commit()
            log.info("用户数据表升级完成")

        self.sql = lk_db.get_table("USERINFO", table_content, 2, update_db)

        def migrate_secret_table(connection, version):
            if version < 1:
                cursor = connection.cursor()
                cursor.execute("DROP TABLE IF EXISTS `SECRET_NEW`")
                cursor.execute(
                    """
                    CREATE TABLE `SECRET_NEW` (
                        ID TEXT PRIMARY KEY,
                        secret TEXT NOT NULL UNIQUE,
                        expires_at REAL NOT NULL
                    )
                    """
                )
                cursor.execute(
                    """
                    INSERT INTO `SECRET_NEW` (ID, secret, expires_at)
                    SELECT CAST(user_id AS TEXT), secret, expires_at FROM `SECRET`
                    """
                )
                cursor.execute("DROP TABLE `SECRET`")
                cursor.execute("ALTER TABLE `SECRET_NEW` RENAME TO `SECRET`")
                cursor.close()
                connection.commit()

        self.secret_table = lk_db.get_table(
            "SECRET",
            "ID TEXT PRIMARY KEY, secret TEXT NOT NULL UNIQUE, expires_at REAL NOT NULL",
            1,
            migrate_secret_table,
        )

        def migrate_account_binding_table(connection, version):
            if version < 1:
                cursor = connection.cursor()
                cursor.execute("DROP TABLE IF EXISTS `ACCOUNTBINDING_NEW`")
                cursor.execute(
                    """
                    CREATE TABLE `ACCOUNTBINDING_NEW` (
                        ID TEXT PRIMARY KEY,
                        MASTER_ID TEXT NOT NULL
                    )
                    """
                )
                cursor.execute(
                    """
                    INSERT INTO `ACCOUNTBINDING_NEW` (ID, MASTER_ID)
                    SELECT CAST(user_id AS TEXT), CAST(master_user_id AS TEXT)
                    FROM `ACCOUNTBINDING`
                    """
                )
                cursor.execute("DROP TABLE `ACCOUNTBINDING`")
                cursor.execute(
                    "ALTER TABLE `ACCOUNTBINDING_NEW` RENAME TO `ACCOUNTBINDING`"
                )
                cursor.close()
                connection.commit()

        self.account_binding_table = lk_db.get_table(
            "ACCOUNTBINDING",
            "ID TEXT PRIMARY KEY, MASTER_ID TEXT NOT NULL",
            1,
            migrate_account_binding_table,
        )
        self._bindings = {
            str(user_id): str(master_user_id)
            for user_id, master_user_id in self.account_binding_table.select_all(
                "ID, MASTER_ID"
            )
        }
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

    def resolve_user_id(self, user_id: str | int) -> str:
        """将绑定账号解析到最终的主账号 ID。"""
        resolved_id = str(user_id)
        visited = set()
        while resolved_id not in visited:
            visited.add(resolved_id)
            master_user_id = self._bindings.get(resolved_id)
            if master_user_id is None:
                return resolved_id
            resolved_id = master_user_id
        raise ValueError(f"账号绑定关系存在循环: {user_id}")

    def create_binding_secret(self, user_id: str | int) -> str | None:
        """为已注册的主账号创建一小时有效的一次性绑定密钥。"""
        master_user_id = self.resolve_user_id(user_id)
        if master_user_id not in self._ids:
            return None
        secret = secrets.token_urlsafe(24)
        with self.sql._conn:
            self.sql._conn.execute(
                """
                INSERT INTO SECRET (ID, secret, expires_at)
                VALUES (?, ?, ?)
                ON CONFLICT(ID) DO UPDATE SET
                    secret = excluded.secret,
                    expires_at = excluded.expires_at
                """,
                (master_user_id, secret, time.time() + 3600),
            )
        return secret

    def bind_account_with_secret(self, user_id: str | int, secret: str) -> str:
        """消费绑定密钥，将调用者账号映射到密钥所属的主账号。"""
        user_id = str(user_id)
        with self.sql._conn:
            secret_data = self.sql._conn.execute(
                "SELECT ID, expires_at FROM SECRET WHERE secret = ?",
                (secret,),
            ).fetchone()
            if secret_data is None:
                return "绑定密钥无效或已使用"

            master_user_id = self.resolve_user_id(str(secret_data[0]))
            if float(secret_data[1]) <= time.time():
                self.sql._conn.execute("DELETE FROM SECRET WHERE secret = ?", (secret,))
                return "绑定密钥已过期，请重新获取"
            if master_user_id not in self._ids:
                return "密钥对应的主账号不存在"
            if user_id == master_user_id:
                return "不能将账号绑定到自身"
            if self.account_binding_table.select("MASTER_ID", {"ID": user_id}):
                return "当前账号已绑定其他主账号，请先解绑"

            self.sql._conn.execute(
                "INSERT INTO ACCOUNTBINDING (ID, MASTER_ID) VALUES (?, ?)",
                (user_id, master_user_id),
            )
            self.sql._conn.execute("DELETE FROM SECRET WHERE secret = ?", (secret,))
        self._bindings[user_id] = master_user_id
        return f"绑定成功，当前账号已关联至主账号 {master_user_id}"

    def force_bind_account(self, user_id: str | int, master_user_id: str | int) -> str:
        """由 MASTER 将账号强制关联到指定的已注册主账号。"""
        user_id = str(user_id)
        master_user_id = self.resolve_user_id(master_user_id)
        if master_user_id not in self._ids:
            return "主账号不存在"
        if user_id == master_user_id:
            return "不能将账号绑定到自身"
        with self.sql._conn:
            self.sql._conn.execute(
                """
                INSERT INTO ACCOUNTBINDING (ID, MASTER_ID)
                VALUES (?, ?)
                ON CONFLICT(ID) DO UPDATE SET
                    MASTER_ID = excluded.MASTER_ID
                """,
                (user_id, master_user_id),
            )
        self._bindings[user_id] = master_user_id
        return f"强制绑定成功，账号 {user_id} 已关联至主账号 {master_user_id}"

    def unbind_account(self, user_id: str | int) -> str:
        """解除调用者账号与主账号的绑定。"""
        user_id = str(user_id)
        with self.sql._conn:
            result = self.sql._conn.execute(
                "DELETE FROM ACCOUNTBINDING WHERE ID = ?", (user_id,)
            )
        self._bindings.pop(user_id, None)
        if result.rowcount == 0:
            return "当前账号没有绑定其他主账号"
        return "解绑成功"

    def force_unbind_account(self, user_id: str | int) -> str:
        """由 MASTER 强制解除指定账号的绑定。"""
        user_id = str(user_id)
        with self.sql._conn:
            result = self.sql._conn.execute(
                "DELETE FROM ACCOUNTBINDING WHERE ID = ?", (user_id,)
            )
        self._bindings.pop(user_id, None)
        if result.rowcount > 0:
            return f"已强制解除账号 {user_id} 的绑定"
        return f"账号 {user_id} 当前没有绑定其他主账号"

    def add_user(self, user_id: str, name: str) -> bool:
        """添加用户"""
        user_id = str(user_id)
        if self.has_user(user_id):
            return False
        if not self.add_name_list(user_id, name):
            return False
        self.user_lock.add_lock(user_id)

        @self.user_lock.lock(user_id)
        def _add_user():
            self.add_user_data(user_id, name)

        _add_user()
        log.debug(f"新增用户{user_id}:{name}")
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
        user_id = self.resolve_user_id(user_id)
        if not self.change_name_list(name, new_name):
            return False

        @self.user_lock.lock(user_id)
        def _change_name():
            user_data = self.get_user_data(user_id)
            user_data.name = new_name
            user_data.save_user_data()
            self.user_name_changed_events.notify(
                UserNameChangedEvent(user_id, new_name)
            )

        _change_name()
        return True

    @_name_lock.run
    def change_name_list(self, name, new_name):
        if new_name in self._names:
            return False
        self._names.remove(name)
        self._names.append(new_name)
        return True

    def sign(self, user_id: str) -> tuple[bool, list]:
        """签到"""
        with self.get_user_data(user_id) as user_data:
            r = self.sign_func(user_data)
        return r

    @staticmethod
    def sign_func(user_data: UserData) -> tuple[bool, list]:
        """签到"""
        today = datetime.now().strftime("%Y-%m-%d")
        if user_data.lastsign == today:
            return False, ["今日已签到"]
        else:
            from ..util import SignInEvent, sign_in_events

            user_data.lastsign = today
            user_data.signdays += 1
            user_data.exp_change(3, True)
            user_data.money_change(10)
            user_data.love_change(1, True)
            return True, sign_in_events.notify(SignInEvent(user_data)).get_result()

    def get_user_name(self, user_id: str) -> str:
        """获取用户名，用户id错误会报错，lk_util.get_name为包装后的方法"""
        return self._ids[self.resolve_user_id(user_id)]

    def has_user(self, user_id: str) -> bool:
        """是否拥有用户,与lk_util.is_valid_user一致"""
        return self.resolve_user_id(user_id) in self._ids

    def load_user_data(self, user_id) -> UserData:
        user_id = self.resolve_user_id(user_id)
        content = self.sql.select(
            "ID, NAME, EXP, MONEY, LASTSIGN, SIGNDAYS, BACKPACK, LOVE, EXPMUL, EXPMULCOUNT, LOVEMUL, LOVEMULCOUNT",
            {"ID": str(user_id)},
        )
        return UserData(content[0], 0)

    def save_user_data(self, user_data: UserData):
        self.sql.update(
            (
                (
                    "NAME",
                    "EXP",
                    "MONEY",
                    "LASTSIGN",
                    "SIGNDAYS",
                    "BACKPACK",
                    "LOVE",
                    "EXPMUL",
                    "EXPMULCOUNT",
                    "LOVEMUL",
                    "LOVEMULCOUNT",
                ),
                (
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
                ),
            ),
            {"ID": user_data.id},
        )

    def add_user_data(self, user_id, name):
        self.sql.insert("ID, NAME", (str(user_id), name))


lk_db = DataBase("lkbot.db")
"""插件专用数据库"""
atexit.register(lk_db.disconnect)
users = Users()
"""管理用户数据"""
