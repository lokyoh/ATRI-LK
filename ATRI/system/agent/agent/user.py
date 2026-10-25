from pydantic import Field, model_validator

from ATRI.system.lkapi.entity.user import user_manager
from ATRI.utils.model import BaseModel

from .database import user_table

#: 存放「亚托莉自己的经历」的保留 ID。
#:
#: 为什么不用某个真实用户的记录：那样她的自我记忆会随对话人变化，
#: 等于把她的人格切成按人头分册——正是 docs §0.1 要消灭的错位。
#: 用一个不可能与 QQ 号冲突的 ID，让这份记忆成为**全局唯一**的一份。
SELF_MEMORY_ID = "__atri_self__"


class User(BaseModel):
    love: int = 50
    memory: list = Field(default_factory=list)
    """关于**这个对话人**的记忆（他/她告诉过我什么）。"""
    profile: str = ""

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_memery(cls, data):
        """兼容历史数据：该字段曾拼错为 memery，旧记录的 JSON 键需迁移到 memory。"""
        if isinstance(data, dict) and "memory" not in data and "memery" in data:
            data = dict(data)
            data["memory"] = data.pop("memery")
        return data


def get_user_info(user_id: str) -> User:
    user_id = user_manager.resolve_user_id(user_id)
    user_info = user_table.select("DATA", f"ID='{user_id}'")
    if len(user_info) == 0:
        user_info = User()
        user_table.insert("ID, DATA", (user_id, user_info.model_dump_json()))
    else:
        user_info = User.model_validate_json(user_info[0][0])
    return user_info


def save_user_info(user_id: str, user_info: User):
    user_id = user_manager.resolve_user_id(user_id)
    user_table.update((("DATA",), (user_info.model_dump_json(),)), f"ID='{user_id}'")


#: 自我记忆的条数上限。和 USER_MEMORY_LIMIT 同量级：
#: 它每轮都会进提示词，条数失控就是成本失控。
SELF_MEMORY_LIMIT = 8


def _read_self_row() -> User:
    rows = user_table.select("DATA", f"ID='{SELF_MEMORY_ID}'")
    if not rows:
        fresh = User()
        user_table.insert("ID, DATA", (SELF_MEMORY_ID, fresh.model_dump_json()))
        return fresh
    return User.model_validate_json(rows[0][0])


def get_self_memories() -> list:
    """取亚托莉自己的经历（全局唯一一份）。

    刻意**不走** ``resolve_user_id``：那不是用户，解析会把保留 ID 当成
    未绑定账号而原样返回，虽然结果一样，但语义上不该混进用户体系。
    """
    try:
        return list(_read_self_row().memory)
    except Exception:
        return []


def add_self_memory(text: str, limit: int = SELF_MEMORY_LIMIT) -> bool:
    """记一条「我自己的经历」，先进先出。"""
    body = (text or "").strip()
    if not body:
        return False
    try:
        row = _read_self_row()
        row.memory.append(body)
        overflow = len(row.memory) - limit
        if overflow > 0:
            del row.memory[:overflow]
        user_table.update(
            (("DATA",), (row.model_dump_json(),)), f"ID='{SELF_MEMORY_ID}'"
        )
        return True
    except Exception:
        return False
