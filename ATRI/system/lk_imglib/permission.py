from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.adapters.qq import GroupMessageCreateEvent
from nonebot.permission import Permission

from ATRI.permission import Admin
from ATRI.system.lkapi.bot import util

from . import plugin_config
from .config import LKImgLibConfig


class GlobalImgLibPermission:
    __slots__ = ()

    async def __call__(self, bot: Bot, event: Event) -> bool:
        config: LKImgLibConfig = plugin_config
        if not config.get("allow_global_store"):
            return False
        user_id = event.get_user_id()
        if util.is_master(user_id):
            return True
        return user_id in config.get("global_lib_permission", [])


class GroupImgLibPermission:
    __slots__ = ()

    async def __call__(self, bot: Bot, event: Event) -> bool:
        config: LKImgLibConfig = plugin_config
        if not config.get("allow_group_store"):
            return False
        if not isinstance(event, GroupMessageEvent) or not isinstance(
            event, GroupMessageCreateEvent
        ):
            return False
        user_id = event.get_user_id()
        group_id = str(event.group_id)
        if group_id in config.get("block_group_store", []):
            return False
        if util.is_master(user_id) or Admin()(bot, event):
            return True
        return user_id in config.get("group_lib_permission", {}).get(group_id, [])


GLOBAL = Permission(GlobalImgLibPermission())
GROUP = Permission(GroupImgLibPermission())
