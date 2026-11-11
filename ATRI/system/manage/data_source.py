from pathlib import Path

from nonebot import get_bot
from nonebot.adapters import Bot
from nonebot.adapters.onebot.v11 import GroupMessageEvent

from ATRI.auth import ban as auth_ban
from ATRI.bot import BotStatus
from ATRI.exceptions import load_error
from ATRI.message import MessageBuilder
from ATRI.service import ServiceTools
from ATRI.utils import FileDealer

from .models import RequestList

MANAGE_DIR = Path(".") / "data" / "plugins" / "manage"
MANAGE_DIR.mkdir(parents=True, exist_ok=True)

_TRACEBACK_FORMAT = (
    MessageBuilder("追踪ID：{trace_id}")
    .text("关键词：{prompt}")
    .text("时间：{time}")
    .text("{content}")
    .done()
)


class BotManager:
    async def __load_data(self, file_name: str) -> dict:
        path = MANAGE_DIR / file_name
        dealer = FileDealer(path)
        if not path.is_file():
            await dealer.write_json(dict())
        try:
            data = dealer.json()
        except Exception:
            data = dict()
        return data

    async def __store_data(self, file_name: str, data: dict) -> None:
        path = MANAGE_DIR / file_name
        dealer = FileDealer(path)
        if not path.is_file():
            await dealer.write_json(dict())
        await dealer.write_json(data)

    async def load_friend_req(self) -> RequestList:
        return RequestList.model_validate(await self.__load_data("friend_add.json"))

    async def store_friend_req(self, data: dict) -> None:
        await self.__store_data("friend_add.json", data)

    async def load_group_req(self) -> RequestList:
        return RequestList.model_validate(await self.__load_data("group_invite.json"))

    async def store_group_req(self, data: dict) -> None:
        await self.__store_data("group_invite.json", data)

    @staticmethod
    async def block_group(
        group_id: str,
        duration_minutes: float = 0,
        reason: str = "",
        operator_id: str = "",
    ) -> None:
        """封禁群聊。

        :param duration_minutes: 时长（分钟），``<= 0`` 表示永久
        """
        await auth_ban.ban(
            auth_ban.TARGET_GROUP,
            group_id,
            minutes=duration_minutes,
            reason=reason,
            operator_id=operator_id,
        )

    @staticmethod
    async def unblock_group(group_id: str) -> None:
        if not await auth_ban.unban(auth_ban.TARGET_GROUP, group_id):
            raise Exception("群不存在于封禁名单")

    @staticmethod
    async def block_user(
        user_id: str,
        duration_minutes: float = 0,
        reason: str = "",
        operator_id: str = "",
    ) -> None:
        """封禁用户。

        :param duration_minutes: 时长（分钟），``<= 0`` 表示永久
        """
        await auth_ban.ban(
            auth_ban.TARGET_USER,
            user_id,
            minutes=duration_minutes,
            reason=reason,
            operator_id=operator_id,
        )

    @staticmethod
    async def unblock_user(user_id: str) -> None:
        if not await auth_ban.unban(auth_ban.TARGET_USER, user_id):
            raise Exception("用户不存在于封禁名单")

    async def toggle_group_enabled(self, event) -> bool:
        """翻转当前群的开关，返回新状态。"""
        if not isinstance(event, GroupMessageEvent):
            raise Exception("该功能只能在群聊中使用")
        return await auth_ban.toggle_group_enabled(str(event.group_id))

    @staticmethod
    async def set_user_level(group_id: str, user_id: str, level: int) -> None:
        await auth_ban.set_user_level(group_id, user_id, level)

    @staticmethod
    async def get_group_levels(group_id: str) -> dict[str, int]:
        return auth_ban.group_cache.levels_in(group_id)

    @staticmethod
    async def toggel_bot_status(bot_id: str) -> bool:
        bot_statu = BotStatus.get_bot_statu(bot_id)
        bot_statu.enable = not bot_statu.enable
        BotStatus.set_bot_status(bot_id, bot_statu)
        return bot_statu.enable

    @staticmethod
    async def get_bot_status(bot_id: str) -> bool:
        bot_statu = BotStatus.get_bot_statu(bot_id)
        return bot_statu.enable

    def toggle_global_service(self, service: str) -> bool:
        serv = ServiceTools(service)
        try:
            data = serv.load_service_config()
        except Exception as e:
            error_msg = str(e)
            raise Exception(error_msg)

        data.enabled = not data.enabled
        serv.save_service_config(data)
        return data.enabled

    def toggle_group_service(self, service: str, event) -> bool:
        if isinstance(event, GroupMessageEvent):
            group_id = str(event.group_id)
            serv = ServiceTools(service)
            try:
                data = serv.load_service_config()
            except Exception as e:
                error_msg = str(e)
                raise Exception(error_msg)

            if group_id in data.disable_group:
                data.disable_group.remove(group_id)
                result = True
            else:
                data.disable_group.append(group_id)
                result = False
            serv.save_service_config(data)
            return result
        raise Exception("该功能只能在群聊中使用")

    def toggle_service_white_list(self, service: str) -> bool:
        serv = ServiceTools(service)
        try:
            data = serv.load_service_config()
        except Exception as e:
            error_msg = str(e)
            raise Exception(error_msg)
        data.white_list_mode = not data.white_list_mode
        serv.save_service_config(data)
        return data.white_list_mode

    def toggle_user_service(self, service: str, user_id: str) -> bool:
        serv = ServiceTools(service)
        try:
            data = serv.load_service_config()
        except Exception as e:
            error_msg = str(e)
            raise Exception(error_msg)

        if user_id in data.disable_user:
            data.disable_user.remove(user_id)
            result = True
        else:
            data.disable_user.append(user_id)
            result = False
        serv.save_service_config(data)
        return result

    def toggle_group_service_white_list(self, service: str, group_id: str):
        serv = ServiceTools(service)
        try:
            data = serv.load_service_config()
        except Exception as e:
            error_msg = str(e)
            raise Exception(error_msg)
        if group_id in data.white_list:
            data.white_list.remove(group_id)
            result = False
        else:
            data.white_list.append(group_id)
            result = True
        serv.save_service_config(data)
        return result

    async def track_error(self, trace_id: str) -> str:
        try:
            data = load_error(trace_id)
        except Exception:
            raise Exception("未找到对应ID的信息")

        return _TRACEBACK_FORMAT.format(
            trace_id=data.track_id,
            prompt=data.prompt,
            time=data.time,
            content=data.content,
        )

    def __get_bot(self) -> Bot:
        try:
            return get_bot()
        except Exception:
            raise Exception("无法获取 bot 实例")

    async def apply_friend_req(self, code: str) -> None:
        bot = self.__get_bot()
        try:
            await bot.call_api("set_friend_add_request", flag=code, approve=True)
        except Exception:
            raise Exception("同意失败，请尝试手动同意")
        raw_data = await self.load_friend_req()
        data = raw_data.data
        data.pop(code)
        raw_data.data = data
        await self.store_friend_req(raw_data.model_dump())

    async def reject_friend_req(self, code: str) -> None:
        bot = self.__get_bot()
        try:
            await bot.call_api("set_friend_add_request", flag=code, approve=False)
        except Exception:
            raise Exception("拒绝失败，请尝试手动拒绝")
        raw_data = await self.load_friend_req()
        data = raw_data.data
        data.pop(code)
        raw_data.data = data
        await self.store_friend_req(raw_data.model_dump())

    async def apply_group_req(self, code: str) -> None:
        bot = self.__get_bot()
        try:
            await bot.call_api(
                "set_group_add_request", flag=code, sub_type="invite", approve=True
            )
        except Exception:
            raise Exception("同意失败，请尝试手动同意")
        raw_data = await self.load_group_req()
        data = raw_data.data
        data.pop(code)
        raw_data.data = data
        await self.store_group_req(raw_data.model_dump())

    async def reject_group_req(self, code: str) -> None:
        bot = self.__get_bot()
        try:
            await bot.call_api(
                "set_group_add_request", flag=code, sub_type="invite", approve=False
            )
        except Exception:
            raise Exception("拒绝失败，请尝试手动拒绝")
        raw_data = await self.load_group_req()
        data = raw_data.data
        data.pop(code)
        raw_data.data = data
        await self.store_group_req(raw_data.model_dump())
