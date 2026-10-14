import json
import os
from threading import Lock
from typing import ClassVar

from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.adapters.qq import GroupMessageCreateEvent
from nonebot.permission import Permission as _Permission

from ATRI import conf
from ATRI.dir import SYS_CONFIG_DIR
from ATRI.utils import FileDealer

MASTER_FILE_PATH = SYS_CONFIG_DIR / "master.json"
MASTER_FILE = FileDealer(MASTER_FILE_PATH)
_superusers = set(conf.BotConfig.superusers)
MASTER_LIST = set(_superusers)
_cache_lock = Lock()
_cached_mtime: float | None = None
_cached_masters: set[str] = MASTER_LIST


def _ensure_master_file() -> None:
    if not MASTER_FILE_PATH.is_file():
        data = {i: {"is_conf": True} for i in _superusers}
        MASTER_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = MASTER_FILE_PATH.with_suffix(MASTER_FILE_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, MASTER_FILE_PATH)


def _load_masters() -> set[str]:
    """带 mtime 缓存的权限集合加载，fail-closed。"""
    global _cached_mtime, _cached_masters, MASTER_LIST
    try:
        mtime = MASTER_FILE_PATH.stat().st_mtime
    except OSError:
        try:
            _ensure_master_file()
            mtime = MASTER_FILE_PATH.stat().st_mtime
        except OSError:
            MASTER_LIST = set(_superusers)
            return MASTER_LIST
    with _cache_lock:
        if _cached_mtime == mtime:
            if MASTER_LIST is not _cached_masters:
                MASTER_LIST = _cached_masters
            return _cached_masters
        try:
            data = json.loads(MASTER_FILE_PATH.read_text(encoding="utf-8"))
            masters = set(data) | _superusers
        except Exception:
            MASTER_LIST = set(_superusers)
            return MASTER_LIST
        _cached_mtime = mtime
        MASTER_LIST = masters
        _cached_masters = masters
        return masters


def is_master(bot: Bot, event: Event) -> bool:
    try:
        user_id = event.get_user_id()
    except Exception:
        return False
    return user_id in _load_masters()


async def toggle_master(user_id: str):
    """
    添加/移除主人
    如存在即移除，如不存在即添加
    """
    data = MASTER_FILE.json()
    if user_id in data:
        data.pop(user_id)
    else:
        data[user_id] = {"is_conf": False}
    await MASTER_FILE.write_json(data)


class Permission(_Permission):
    name = "UnknownPermission"

    def set_name(self, name: str) -> "Permission":
        """为当前权限设置名称

        Args:
            name (str): 权限的名称

        Returns:
            Permission: self
        """
        self.name = name
        return self


class Master:
    """检查当前事件是否属于主人"""

    __slots__ = ()

    async def __call__(self, bot: Bot, event: Event) -> bool:
        return is_master(bot, event)


class Admin:
    """
    检查当前事件是否属于管理员
    其包括：主人、群主、群管理
    """

    __slots__ = ()

    GROUP_ADMIN: ClassVar = ["admin", "owner"]

    async def __call__(self, bot: Bot, event: Event) -> bool:
        if isinstance(event, GroupMessageEvent):
            return event.sender.role in self.GROUP_ADMIN or is_master(bot, event)
        if isinstance(event, GroupMessageCreateEvent):
            return event.author.member_role in self.GROUP_ADMIN or is_master(bot, event)
        else:
            return False


_load_masters()
MASTER = Permission(Master()).set_name("Master")
ADMIN = Permission(Admin()).set_name("Admin")
