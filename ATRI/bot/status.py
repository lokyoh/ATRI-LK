import json
import os

from nonebot.adapters.onebot.v11 import Bot, Event
from nonebot.exception import IgnoredException
from nonebot.message import run_preprocessor

from ATRI import driver
from ATRI.dir import SYS_CONFIG_DIR
from ATRI.permission import MASTER_LIST
from ATRI.utils.model import BaseModel

BOT_STATUS_FILE_PATH = SYS_CONFIG_DIR / "bot_status.json"
GLOBAL_STATUS_FILE_PATH = SYS_CONFIG_DIR / "global_status.json"


class Statu(BaseModel):
    enable: bool = True
    is_group_black_list: bool = True
    is_user_black_list: bool = True
    group_black_list: list[str] = []
    user_black_list: list[str] = []
    group_white_list: list[str] = []
    user_white_list: list[str] = []


class BotStatus:
    data: dict[str, Statu] = {}

    @classmethod
    def add_bot_statu(cls, bot_id: str) -> None:
        cls.data[bot_id] = cls.get_bot_statu(bot_id)

    @classmethod
    def remove_bot_statu(cls, bot_id: str) -> None:
        cls.set_bot_status(bot_id, cls.get_bot_statu(bot_id))
        del cls.data[bot_id]

    @classmethod
    def get_bot_status(cls) -> dict[str, Statu]:
        if not os.path.exists(BOT_STATUS_FILE_PATH):
            with open(BOT_STATUS_FILE_PATH, "w", encoding="utf-8") as file:
                json.dump(dict(), file, ensure_ascii=False, indent=4)
        with open(BOT_STATUS_FILE_PATH, "r", encoding="utf-8") as file:
            return json.load(file)

    @classmethod
    def get_bot_statu(cls, bot_id: str) -> Statu:
        return cls.data.get(bot_id, Statu())

    @classmethod
    def set_bot_status(cls, bot_id: str, statu: Statu) -> None:
        bot_status = cls.get_bot_status()
        bot_status[bot_id] = statu.model_dump()
        with open(BOT_STATUS_FILE_PATH, "w", encoding="utf-8") as file:
            json.dump(bot_status, file, ensure_ascii=False, indent=4)

    @classmethod
    def is_blocked(
        cls, bot_id: str, user_id: str | None = None, group_id: str | None = None
    ) -> bool:
        """
        判断机器人规则是否阻挡。
        :param bot_id: 机器人ID
        :param user_id: 用户ID
        :param group_id: 群聊ID
        :return: 是否被阻挡
        """
        statu = cls.get_bot_statu(bot_id)
        if not statu.enable:
            return True
        if user_id:
            if user_id in MASTER_LIST:
                return False
            if statu.is_user_black_list:
                if user_id in statu.user_black_list:
                    return True
            else:
                if user_id not in statu.user_white_list:
                    return True
        if group_id:
            if statu.is_group_black_list:
                if group_id in statu.group_black_list:
                    return True
            else:
                if group_id not in statu.group_white_list:
                    return True
        return False


class GlobalStatusModel:
    def __init__(self):
        self.status = Statu()
        self.read_from_file()

    def is_blocked(
        self, user_id: str | None = None, group_id: str | None = None
    ) -> bool:
        if not self.status.enable:
            return True
        if user_id:
            if self.status.is_user_black_list:
                if user_id in self.status.user_black_list:
                    return True
            else:
                if user_id not in self.status.user_white_list:
                    return True
        if group_id:
            if self.status.is_group_black_list:
                if group_id in self.status.group_black_list:
                    return True
            else:
                if group_id not in self.status.group_white_list:
                    return True
        return False

    def save_to_file(self) -> None:
        self.status.write_into_file(GLOBAL_STATUS_FILE_PATH)

    def read_from_file(self) -> None:
        if not os.path.exists(GLOBAL_STATUS_FILE_PATH):
            self.save_to_file()
        else:
            self.status = Statu.read_from_file(GLOBAL_STATUS_FILE_PATH)


GlobalStatus: GlobalStatusModel = GlobalStatusModel()
"""全局状态"""


@driver().on_bot_connect
def _(bot: Bot):
    bot_id = str(bot.self_id)
    BotStatus.add_bot_statu(bot_id)


@driver().on_bot_disconnect
def _(bot: Bot):
    bot_id = str(bot.self_id)
    BotStatus.remove_bot_statu(bot_id)


@run_preprocessor
async def _(event: Event):
    bot_id = str(event.self_id)
    user_id = str(getattr(event, "user_id", ""))
    group_id = str(getattr(event, "group_id", ""))

    if GlobalStatus.is_blocked(user_id, group_id):
        raise IgnoredException(
            f"Blocked by GlobalStatus: user_id={user_id}, group_id={group_id}"
        )
    if BotStatus.is_blocked(bot_id, user_id, group_id):
        raise IgnoredException(
            f"Blocked by BotStatus: bot_id={bot_id}, user_id={user_id}, group_id={group_id}"
        )
