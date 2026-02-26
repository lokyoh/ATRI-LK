from nonebot.adapters.onebot.v11 import Bot, Message

from ATRI.exceptions import save_error, str_traceback
from ATRI.log import log

from .model import Group, User
from .statistics import manual_add_server_statistic


class BotUtils:
    @classmethod
    def get_user_avatar_url(
        cls, user_id: str, platform: str, appid: str | None = None
    ) -> str | None:
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
        """
        获取好友列表。
        :param bot: Bot对象
        :return: 好友列表
        """
        raw_friend_list = await bot.get_friend_list()
        friend_list = [User.model_validate(f) for f in raw_friend_list]
        return friend_list

    @classmethod
    async def get_group_list(cls, bot: Bot) -> list[Group]:
        """
        获取群列表。
        :param bot: Bot对象
        :return: 群列表
        """
        raw_group_list = await bot.get_group_list()
        group_list = [Group.model_validate(g) for g in raw_group_list]
        return group_list

    @classmethod
    async def send_message(
        cls,
        bot: Bot,
        service: str,
        message: str | Message,
        user_id: str | None = None,
        group_id: str | None = None,
    ):
        """
        发送消息并添加服务调用统计记录。
        :param bot: Bot对象
        :param service: 服务名
        :param user_id: 用户ID，私聊消息时必填
        :param group_id: 群ID，群消息时必填
        :param message: 消息内容
        """
        track_id = None
        target_id = None
        target_type = None
        try:
            if user_id:
                target_id = user_id
                target_type = "private"
                await bot.send_private_msg(user_id=int(user_id), message=message)
            elif group_id:
                target_id = group_id
                target_type = "group"
                await bot.send_group_msg(group_id=int(group_id), message=message)
        except Exception as e:
            prompt = "发送错误 请参考协议端输出"
            error_message = str_traceback(e)
            track_id = save_error(prompt, error_message)
            log.error(f"{service} send_message ActionFailed:\n{error_message}")
        manual_add_server_statistic(
            bot=bot,
            service=service,
            call_type="send_message",
            target_id=target_id,
            target_type=target_type,
            track_id=track_id,
        )
