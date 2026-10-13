from nonebot.adapters import Bot
from nonebot.adapters.onebot.v11 import Bot as OneBot
from nonebot.adapters.qq import Bot as QQBot

from ATRI.log import log
from ATRI.system.lkapi.bot import util as lk_util
from ATRI.system.lkapi.entity.user import user_manager

members = {}


async def get_name(bot: Bot, user_id: str, chat_id: str):
    if lk_util.is_valid_user(user_id):
        return lk_util.get_name(user_id)
    if isinstance(bot, OneBot):
        group_id = int(chat_id)
        user_id = int(user_id)
        try:
            if group_id not in members:
                members[group_id] = {}
            if user_id not in members[group_id]:
                member_list = await bot.get_group_member_list(group_id=group_id)
                for m in member_list:
                    uid = int(m["user_id"])
                    members[group_id][uid] = m.get("nickname")
            return members[group_id][user_id]
        except Exception:
            log.warning(f"获取QQ用户{user_id}在群{group_id}昵称错误")
    if isinstance(bot, QQBot):
        try:
            if group_id not in members:
                members[group_id] = {}
            if user_id not in members[group_id]:
                member = await bot.get_group_member(
                    group_openid=group_id, member_openid=user_id
                )
                members[group_id][uid] = member.username
            return members[group_id][user_id]
        except Exception:
            log.warning(f"获取官方QQ用户{user_id}在群{group_id}昵称错误")
    return str(user_id)


def get_user_group(user_id):
    user_manager.resolve_user_id(user_id)
    user_group = "用户"
    if lk_util.is_master(user_id):
        user_group = "主人"
    return user_group
