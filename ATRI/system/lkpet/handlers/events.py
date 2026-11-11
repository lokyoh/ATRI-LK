"""Non-command event handlers merged in from the legacy ``pet_data.py``.

ATRI's ``BaseEvents`` rejects coroutine listeners, so all handlers stay
synchronous and use the synchronous lookups in ``database.py``.

Three LK account hooks live here:

* ``item_loading_events`` — 注册肉鸽入场券（物品表在每次加载时重建，必须重挂）
* ``sign_in_events``      — 签到发放入场券
* ``user_info_events``    — 用户信息卡展示宠物名
"""

from ATRI.log import log
from ATRI.system.lkapi.bot import util as lk_util
from ATRI.system.lkapi.bot.events import (
    SignInEvent,
    UserInfoEvent,
    item_loading_events,
    sign_in_events,
    user_info_events,
)
from ATRI.system.lkapi.entity.user import UserNameChangedEvent, user_manager

from .. import config as plugin_config
from ..core.chat import chat_manager
from ..database import load_pet_name
from ..items import register_rogue_items, rogue_ticket_name


@item_loading_events.handle()
def lkpet_item_loading():
    """把肉鸽入场券注册进 LK 物品表（物品表每次重载都会清空，故在此重挂）。"""
    register_rogue_items()


@sign_in_events.handle()
def lkpet_sign_in(event: SignInEvent):
    """签到赠送肉鸽入场券，让「每天一次入场」与签到挂钩。"""
    count = int(plugin_config.get("rogue_ticket_per_sign", 1) or 0)
    if count <= 0:
        return
    item_name = rogue_ticket_name()
    if not lk_util.item_change_func(event.user_data, item_name, count):
        log.warning(f"签到发放入场券失败：{item_name} ×{count}")
        return
    event.add_result(f"获得{count}张{item_name}（凭券入场不消耗行动点）")


@user_info_events.handle()
def lkpet_user_info(event: UserInfoEvent):
    """Show the player's pet name in the user-info card."""
    user_id = str(event.user_data.id)
    name = load_pet_name(user_id)
    event.add_result(f"宠物:{name if name else '无宠物'}", 20)


@user_manager.user_name_changed_events.handle()
def lkpet_user_name_changed(event: UserNameChangedEvent):
    """Keep the chat persona's 主人 name in sync with the user's nickname."""
    chat_manager.rename_user(event.user_id, event.new_name)
