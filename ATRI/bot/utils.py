from nonebot.adapters.onebot.v11 import Bot, Message

from .model import User, Group


class BotUtils:
    @classmethod
    def get_user_avatar_url(cls, user_id: str, platform: str, appid: str | None = None) -> str | None:
        """
        快捷获取用户头像url
        :param user_id: 用户id
        :param platform: 平台
        :param appid: 官qid
        """
        if platform != "qq":
            return None
        if user_id.isdigit():
            return f"http://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"
        else:
            return f"https://q.qlogo.cn/qqapp/{appid}/{user_id}/640"

    @classmethod
    async def get_friend_list(cls, bot: Bot) -> list[User]:
        raw_friend_list = await bot.get_friend_list()
        friend_list = [User.model_validate(f) for f in raw_friend_list]
        return friend_list

    @classmethod
    async def get_group_list(cls, bot: Bot) -> list[Group]:
        raw_group_list = await bot.get_group_list()
        group_list = [Group.model_validate(g) for g in raw_group_list]
        return group_list

    @classmethod
    async def send_message(cls, bot: Bot, user_id: str | None, group_id: str | None, message: str | Message):
        if user_id:
            await bot.send_private_msg(user_id=int(user_id), message=message)
        elif user_id:
            await bot.send_group_msg(group_id=int(group_id), message=message)
        return None
