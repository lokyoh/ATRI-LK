"""Non-command event handlers merged in from the legacy ``pet_data.py``.

ATRI's ``BaseEvents`` rejects coroutine listeners, so both handlers stay
synchronous and use the synchronous lookups in ``database.py``.
"""

from ATRI.system.lkapi.bot.events import UserInfoEvent, user_info_events
from ATRI.system.lkapi.entity.user import UserNameChangedEvent, user_manager

from ..core.chat import chat_manager
from ..database import load_pet_name


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
