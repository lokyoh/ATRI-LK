import nonebot

from ATRI.bot.utils import BotUtils

from ....config import AVA_URL, GROUP_AVA_URL
from .model import (
    GroupDetail,
    Plugin,
    ReqResult,
    Task,
    UpdateGroup,
    UserDetail,
)


class ApiDataSource:
    @classmethod
    async def update_group(cls, group: UpdateGroup):
        """更新群组数据

        参数:
            group: UpdateGroup
        """
        pass

    @classmethod
    async def get_request_list(cls) -> ReqResult:
        """获取好友与群组请求列表

        返回:
            ReqResult: 数据内容
        """
        req_result = ReqResult()
        return req_result

    @classmethod
    async def get_friend_detail(cls, bot_id: str, user_id: str) -> UserDetail | None:
        """获取好友详情

        参数:
            bot_id: bot id
            user_id: 用户id

        返回:
            UserDetail | None: 详情数据
        """
        bot = nonebot.get_bot(bot_id)
        friend_list = await BotUtils.get_friend_list(bot)
        fd = [x for x in friend_list if x.user_id == user_id]
        if not fd:
            return None
        like_plugin = {}
        user = fd[0]
        return UserDetail(
            user_id=user_id,
            ava_url=AVA_URL.format(user_id),
            nickname=user.nickname,
            remark="",
            is_ban=False,
            chat_count=0,
            call_count=0,
            like_plugin=like_plugin,
        )

    @classmethod
    async def __get_group_detail_like_plugin(cls, group_id: str) -> dict[str, int]:
        """获取群组喜爱的插件

        参数:
            group_id: 群组id

        返回:
            dict[str, int]: 插件与调用次数
        """
        like_plugin = {}
        return like_plugin

    @classmethod
    async def __get_group_detail_disable_plugin(
        cls, group: str
    ) -> list[Plugin]:
        """获取群组禁用插件

        参数:
            group: GroupConsole

        返回:
            list[Plugin]: 禁用插件数据列表
        """
        disable_plugins: list[Plugin] = []
        return disable_plugins

    @classmethod
    async def __get_group_detail_task(cls, group: str) -> list[Task]:
        """获取群组被动技能状态

        参数:
            group: GroupConsole

        返回:
            list[Task]: 群组被动列表
        """
        task_list = []
        return task_list

    @classmethod
    async def get_group_detail(cls, group_id: str) -> GroupDetail | None:
        """获取群组详情

        参数:
            group_id: 群组id

        返回:
            GroupDetail | None: 群组详情数据
        """
        group = group_id
        if not group:
            return None
        like_plugin = await cls.__get_group_detail_like_plugin(group_id)
        disable_plugins: list[Plugin] = await cls.__get_group_detail_disable_plugin(
            group
        )
        task_list = await cls.__get_group_detail_task(group)
        return GroupDetail(
            group_id=group_id,
            ava_url=GROUP_AVA_URL.format(group_id, group_id),
            name="",
            member_count=0,
            max_member_count=0,
            chat_count=0,
            call_count=0,
            like_plugin=like_plugin,
            level=0,
            status=False,
            close_plugins=disable_plugins,
            task=task_list,
        )
