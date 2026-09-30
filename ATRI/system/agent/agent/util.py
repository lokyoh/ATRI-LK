from ATRI.log import log
from ATRI.system.lkapi.bot import util as lk_util

members = {}


async def get_name(bot, user_id: str, chat_id: str):
    if chat_id and chat_id[0].isdigit():
        group_id = int(chat_id)
        user_id = int(user_id)
        if lk_util.is_valid_user(user_id):
            return lk_util.get_name(user_id)
        else:
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
    if chat_id.startswith("qq_") and lk_util.is_valid_user(user_id):
        return lk_util.get_name(user_id)
    return str(user_id)


def get_user_group(user_id):
    user_group = "用户"
    if lk_util.is_master(user_id):
        user_group = "主人"
    return user_group
