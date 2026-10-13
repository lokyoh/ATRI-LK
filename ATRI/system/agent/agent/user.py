from typing import ClassVar

from ATRI.system.lkapi.entity.user import user_manager
from ATRI.utils.model import BaseModel

from .database import user_table


class User(BaseModel):
    love: int = 50
    memery: ClassVar[list] = []
    profile: str = ""


def get_user_info(user_id: str) -> User:
    user_id = user_manager.resolve_user_id(user_id)
    user_info = user_table.select("DATA", f"ID={user_id}")
    if len(user_info) == 0:
        user_info = User()
        user_table.insert("ID, DATA", (int(user_id), user_info.model_dump_json()))
    else:
        user_info = User.model_validate_json(user_info[0][0])
    return user_info


def save_user_info(user_id: str, user_info: User):
    user_id = user_manager.resolve_user_id(user_id)
    user_table.update((("DATA",), (user_info.model_dump_json(),)), f"ID={user_id}")
