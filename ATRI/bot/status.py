import json
import os
from typing import ClassVar

import anyio
from nonebot.adapters import Bot, Event
from nonebot.exception import IgnoredException
from nonebot.message import run_preprocessor
from pydantic import Field

from ATRI import driver
from ATRI.bot.model import PanelItem
from ATRI.dir import SYS_CONFIG_DIR
from ATRI.permission import MASTER_LIST
from ATRI.utils.model import BaseModel

BOT_STATUS_FILE_PATH = SYS_CONFIG_DIR / "bot_status.json"
GLOBAL_STATUS_FILE_PATH = SYS_CONFIG_DIR / "global_status.json"
QQ_PANEL_PATH = SYS_CONFIG_DIR / "panel.json"
panel_status = []


class Statu(BaseModel):
    enable: bool = True
    is_group_black_list: bool = True
    is_user_black_list: bool = True
    group_black_list: list[str] = Field(default_factory=list)
    user_black_list: list[str] = Field(default_factory=list)
    group_white_list: list[str] = Field(default_factory=list)
    user_white_list: list[str] = Field(default_factory=list)


class BotStatus:
    data: ClassVar[dict[str, Statu]] = {}

    @classmethod
    def add_bot_statu(cls, bot_id: str) -> None:
        cls.data[bot_id] = cls.get_bot_statu(bot_id)

    @classmethod
    def remove_bot_statu(cls, bot_id: str) -> None:
        cls.set_bot_status(bot_id, cls.get_bot_statu(bot_id))
        del cls.data[bot_id]

    @classmethod
    def get_bot_status(cls) -> dict[str, dict]:
        if not os.path.exists(BOT_STATUS_FILE_PATH):
            with open(BOT_STATUS_FILE_PATH, "w", encoding="utf-8") as file:
                json.dump({}, file, ensure_ascii=False, indent=4)
        with open(BOT_STATUS_FILE_PATH, encoding="utf-8") as file:
            return json.load(file)

    @classmethod
    def get_bot_statu(cls, bot_id: str) -> Statu:
        if bot_id not in cls.data:
            cls.data[bot_id] = Statu.model_validate(
                cls.get_bot_status().get(bot_id, {})
            )
        return cls.data[bot_id]

    @classmethod
    def set_bot_status(cls, bot_id: str, statu: Statu) -> None:
        bot_status = cls.get_bot_status()
        bot_status[bot_id] = statu.model_dump()
        with open(BOT_STATUS_FILE_PATH, "w", encoding="utf-8") as file:
            json.dump(bot_status, file, ensure_ascii=False, indent=4)
        cls.data[bot_id] = statu

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


async def _save_panel(re):
    async with await anyio.open_file(QQ_PANEL_PATH, "w", encoding="utf-8") as f:
        await f.write(json.dumps(re))


def _compare_panel(panel_items: list[PanelItem], online_panel: list[dict]):
    if len(panel_items) != len(online_panel):
        return False
    from .utils import BotUtils

    cmd = BotUtils.get_command_start()
    for item in online_panel:
        if item.get("type") == "command" and not item["name"].startswith(cmd):
            item["name"] = cmd + item["name"]
    online_panel = [PanelItem(**p) for p in online_panel]

    def key(x):
        return (x.name, x.desc, x.type or "", x.only_admin, x.link or "")

    a = sorted(panel_items, key=key)
    b = sorted(online_panel, key=key)
    return a == b


@driver().on_bot_connect
async def _(bot: Bot):
    bot_id = str(bot.self_id)
    BotStatus.add_bot_statu(bot_id)
    # if isinstance(bot, QQBot):
    #     if bot_id in panel_status:
    #         return
    #     panel_status.append(bot_id)
    #     from .utils import BotUtils

    #     panel_items = await ATRIEventBus.publish("set_panel", source="ATRI")
    #     if panel_items:
    #         panel = Panel(items=panel_items)
    #         if not os.path.exists(QQ_PANEL_PATH):
    #             new_panel = NewPanel(scope="group", target_type="all", panel=panel)
    #             re = await BotUtils.add_qq_panel(bot, new_panel)
    #             pid = re["panel_id"]
    #             log.info(f"创建面板:{pid}")
    #             await _save_panel(re)
    #             return
    #         async with await anyio.open_file(QQ_PANEL_PATH, "r", encoding="utf-8") as f:
    #             content = json.loads(await f.read())
    #         pid = content["panel_id"]
    #         re = await BotUtils.get_qq_panel(bot, pid)
    #         online_panel = re["panel"]["items"]
    #         if not _compare_panel(panel_items, online_panel):
    #             log.info(f"重构面板:{pid}")
    #             panel.remark = f"从{re['version']}重构面板"
    #             re = await BotUtils.set_qq_panel(bot, pid, panel)
    #             log.info(f"面板版本:{re['version']}")


@driver().on_bot_disconnect
async def _(bot: Bot):
    bot_id = str(bot.self_id)
    BotStatus.remove_bot_statu(bot_id)


@run_preprocessor
async def _(event: Event, bot: Bot):
    bot_id = str(bot.self_id)
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
