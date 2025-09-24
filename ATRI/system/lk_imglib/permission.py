from nonebot.adapters import Bot, Event
from nonebot.permission import Permission
from nonebot.adapters.onebot.v11 import GroupMessageEvent

from ATRI.system.lkapi.bot import util

from . import plugin_config
from .config import LKImgLibConfig


class GlobalImgLibPermission:
    __slots__ = ()

    async def __call__(self, bot: Bot, event: Event) -> bool:
        config: LKImgLibConfig = plugin_config.config()
        if not config.allow_global_store:
            return False
        user_id = event.get_user_id()
        if util.is_master(user_id):
            return True
        if user_id in config.global_lib_permission:
            return True
        return False


class GroupImgLibPermission:
    __slots__ = ()

    async def __call__(self, bot: Bot, event: Event) -> bool:
        config: LKImgLibConfig = plugin_config.config()
        if not config.allow_group_store:
            return False
        if not isinstance(event, GroupMessageEvent):
            return False
        user_id = event.get_user_id()
        group_id = str(event.group_id)
        if group_id in config.block_group_store:
            return False
        if util.is_master(user_id) and event.sender.role in ["admin", "owner"]:
            return True
        if user_id in config.group_lib_permission.get(group_id, []):
            return True
        return False


GLOBAL = Permission(GlobalImgLibPermission())
GROUP = Permission(GroupImgLibPermission())
