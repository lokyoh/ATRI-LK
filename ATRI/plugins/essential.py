import asyncio
from pathlib import Path
from random import choice, randint

from nonebot.adapters.onebot.v11 import (
    GroupIncreaseNoticeEvent,
    GroupDecreaseNoticeEvent,
    GroupAdminNoticeEvent,
    GroupBanNoticeEvent,
)

from ATRI import conf
from ATRI.message import MessageBuilder
from ATRI.service import Service

__ESSENTIAL_DIR = Path(".") / "data" / "plugins" / "essential"
__TEMP_DIR = Path(".") / "data" / "temp"
__ESSENTIAL_DIR.mkdir(parents=True, exist_ok=True)
__TEMP_DIR.mkdir(parents=True, exist_ok=True)

plugin = Service("基础部件").document("对基础请求进行处理").type(Service.ServiceType.HIDDEN)

group_member_event = plugin.on_notice("群成员变动", "群成员变动检测")


@group_member_event.handle()
async def _(event: GroupIncreaseNoticeEvent):
    await asyncio.sleep(randint(1, 6))
    await group_member_event.finish(
        MessageBuilder("好欸! 事新人!").at(user_id=event.user_id).text(f"在下 {choice(list(conf.BotConfig.nickname))}")
    )


@group_member_event.handle()
async def _(event: GroupDecreaseNoticeEvent):
    await asyncio.sleep(randint(1, 6))
    await group_member_event.finish("呜——有人跑了...")


group_admin_event = plugin.on_notice("群管理变动", "群管理变动检测")


@group_admin_event.handle()
async def _(event: GroupAdminNoticeEvent):
    sub_type = event.sub_type
    if event.is_tome() and sub_type == "set":
        await plugin.send_to_master(f"好欸! 咱在群 {event.group_id} 成为了管理!!")
        return
    elif sub_type == "set":
        await group_admin_event.finish("新的py交易已达成")
    else:
        await group_admin_event.finish("有的人超能力到期了 :(")


group_ban_event = plugin.on_notice("群禁言变动", "群禁言变动检测")


@group_ban_event.handle()
async def _(event: GroupBanNoticeEvent):
    if not event.is_tome():
        if event.sub_type == "ban":
            await group_ban_event.finish("群友喝下管理的红茶昏睡了过去")
        else:
            await group_ban_event.finish("群友被管理意外弄醒了")

    if event.duration:
        await plugin.send_to_master(
            MessageBuilder("那个...")
            .text(f"咱在群 {event.group_id} 被 {event.operator_id} 塞上了口球...")
            .text(f"时长...是 {event.duration} 秒")
        )
    else:
        await plugin.send_to_master(
            MessageBuilder("好欸!").text(
                f"咱在群 {event.group_id} 的口球被 {event.operator_id} 解除了!"
            )
        )
