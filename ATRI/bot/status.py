import json
import os
from typing import ClassVar

import anyio
from nonebot.adapters import Bot
from pydantic import Field

from ATRI import driver
from ATRI.auth import ban
from ATRI.auth.reason import BlockReason
from ATRI.bot.model import PanelItem
from ATRI.dir import SYS_CONFIG_DIR
from ATRI.permission import is_master_user
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
    def check_blocked(
        cls, bot_id: str, user_id: str | None = None, group_id: str | None = None
    ) -> BlockReason | None:
        """判断机器人规则是否阻挡，返回原因码。

        黑名单优先走 :class:`ATRI.auth.ban.BanCache`（数据库快照）；快照还没
        加载时退回 ``Statu`` 里的旧 JSON 名单。

        :param bot_id: 机器人ID
        :param user_id: 用户ID
        :param group_id: 群聊ID
        :return: 拦截原因；未拦截返回 ``None``
        """
        statu = cls.get_bot_statu(bot_id)
        if not statu.enable:
            return BlockReason.BOT_DISABLED
        if user_id:
            if is_master_user(user_id):
                return None
            if ban.is_banned(ban.TARGET_USER, user_id, statu.user_black_list):
                return BlockReason.USER_BANNED
            if not statu.is_user_black_list and user_id not in statu.user_white_list:
                return BlockReason.USER_NOT_ALLOWED
        if group_id:
            if ban.is_banned(ban.TARGET_GROUP, group_id, statu.group_black_list):
                return BlockReason.GROUP_BANNED
            if not statu.is_group_black_list and group_id not in statu.group_white_list:
                return BlockReason.GROUP_NOT_ALLOWED
        return None

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
        return cls.check_blocked(bot_id, user_id, group_id) is not None

    @classmethod
    def sync_from_cache(cls, bot_id: str) -> Statu:
        """用封禁快照重建该机器人的黑名单兼容视图。"""
        statu = cls.get_bot_statu(bot_id)
        if ban.ban_cache.loaded:
            statu.user_black_list = ban.ban_cache.active_ids(ban.TARGET_USER)
            statu.group_black_list = ban.ban_cache.active_ids(ban.TARGET_GROUP)
        return statu


class GlobalStatusModel:
    def __init__(self):
        self.status = Statu()
        self.read_from_file()

    def check_blocked(
        self, user_id: str | None = None, group_id: str | None = None
    ) -> BlockReason | None:
        """判断全局状态是否阻挡，返回原因码。

        黑名单走 :class:`ATRI.auth.ban.BanCache`，快照未加载时退回旧 JSON
        名单；群控制台关闭的群直接拦截。
        """
        if not self.status.enable:
            return BlockReason.GLOBAL_DISABLED
        if user_id:
            if ban.is_banned(ban.TARGET_USER, user_id, self.status.user_black_list):
                return BlockReason.USER_BANNED
            if (
                not self.status.is_user_black_list
                and user_id not in self.status.user_white_list
            ):
                return BlockReason.USER_NOT_ALLOWED
        if group_id:
            if ban.is_banned(ban.TARGET_GROUP, group_id, self.status.group_black_list):
                return BlockReason.GROUP_BANNED
            if (
                not self.status.is_group_black_list
                and group_id not in self.status.group_white_list
            ):
                return BlockReason.GROUP_NOT_ALLOWED
            if not ban.group_cache.is_enabled(group_id):
                return BlockReason.GROUP_DISABLED
        return None

    def sync_from_cache(self) -> None:
        """用封禁快照重建 ``Statu`` 里的黑名单（兼容视图）。

        快照还没加载时不做任何事，避免把名单清空。
        """
        if not ban.ban_cache.loaded:
            return
        self.status.user_black_list = ban.ban_cache.active_ids(ban.TARGET_USER)
        self.status.group_black_list = ban.ban_cache.active_ids(ban.TARGET_GROUP)

    def is_blocked(
        self, user_id: str | None = None, group_id: str | None = None
    ) -> bool:
        return self.check_blocked(user_id, group_id) is not None

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
