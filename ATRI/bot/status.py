import json
import os

from nonebot.adapters.onebot.v11 import Bot
from pydantic import BaseModel

from ATRI import driver
from ATRI.dir import SYS_CONFIG_DIR

BOT_STATUS_FILE_PATH = SYS_CONFIG_DIR / "bot_status.json"


class BotStatu(BaseModel):
    enable: bool = True
    is_black_list: bool = True
    group_list: list[str] = []
    user_list: list[str] = []


class BotStatus:
    data: dict[str, BotStatu] = {}

    @classmethod
    def add_bot_statu(cls, bot_id: str) -> None:
        cls.data[bot_id] = cls.get_bot_statu(bot_id)

    @classmethod
    def remove_bot_statu(cls, bot_id: str) -> None:
        cls.set_bot_status(bot_id, cls.get_bot_statu(bot_id))
        del cls.data[bot_id]

    @classmethod
    def get_bot_status(cls) -> dict[str, BotStatu]:
        if not os.path.exists(BOT_STATUS_FILE_PATH):
            with open(BOT_STATUS_FILE_PATH, "w", encoding="utf-8") as file:
                json.dump(dict(), file, ensure_ascii=False, indent=4)
        with open(BOT_STATUS_FILE_PATH, "r", encoding="utf-8") as file:
            return json.load(file)

    @classmethod
    def get_bot_statu(cls, bot_id: str) -> BotStatu:
        return cls.data.get(bot_id, BotStatu())

    @classmethod
    def set_bot_status(cls, bot_id: str, statu: BotStatu) -> None:
        bot_status = cls.get_bot_status()
        bot_status[bot_id] = statu.model_dump()
        with open(BOT_STATUS_FILE_PATH, "w", encoding="utf-8") as file:
            json.dump(bot_status, file, ensure_ascii=False, indent=4)

    @classmethod
    def is_blocked(cls, bot_id: str, user_id: str | None = None, group_id: str | None = None) -> bool:
        statu = cls.get_bot_statu(bot_id)
        if not statu.enable:
            return True
        if statu.is_black_list:
            if group_id and group_id in statu.group_list:
                return True
            if user_id and user_id in statu.user_list:
                return True
        else:
            if group_id and group_id not in statu.group_list:
                return True
            if user_id and user_id not in statu.user_list:
                return True
        return False

@driver().on_bot_connect
def _(bot: Bot):
    bot_id = str(bot.self_id)
    BotStatus.add_bot_statu(bot_id)


@driver().on_bot_disconnect
def _(bot: Bot):
    bot_id = str(bot.self_id)
    BotStatus.remove_bot_statu(bot_id)
