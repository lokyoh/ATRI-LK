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
    """长期关系评价（-1000~1000）。"""

    familiarity: float = 0.0
    """熟络度 0.0~1.0：聊得越多越熟。"""

    mood_tag: str = ""
    """当下对这个人的态度标签，空串表示没有特别态度。"""

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


def bump_familiarity(user_id: str, delta: float = 0.02) -> None:
    """聊一轮涨一点熟络度，上限 1.0。"""
    try:
        info = get_user_info(user_id)
        new_value = min(1.0, max(0.0, float(info.familiarity) + float(delta)))
        if abs(new_value - float(info.familiarity)) < 1e-9:
            return
        info.familiarity = new_value
        save_user_info(user_id, info)
    except Exception:
        pass


_FAMILIARITY_BANDS = (
    (0.7, "很熟，说话不用客气"),
    (0.4, "比较熟，可以聊得随意些"),
    (0.15, "有点熟了，但还是客气的"),
    (0.0, "还不太熟"),
)


def describe_relation(info: User) -> str:
    """把好感度 + 熟络度 + 态度标签渲染成一段关系描述。"""
    band = "还不太熟"
    for lower, text in _FAMILIARITY_BANDS:
        if float(info.familiarity or 0.0) >= lower:
            band = text
            break

    parts = [band]
    love = int(info.love or 0)
    if love >= 500:
        parts.append("你很喜欢这个人")
    elif love >= 150:
        parts.append("你对这个人有好感")
    elif love <= -500:
        parts.append("你很反感这个人")
    elif love <= -150:
        parts.append("你对这个人有些不满")

    tag = (info.mood_tag or "").strip()
    if tag:
        parts.append(f"当前态度：{tag}")

    return "；".join(parts)


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


def add_self_memory(
    text: str, limit: int = SELF_MEMORY_LIMIT, emotion: float = 0.0
) -> bool:
    """记一条「我自己的经历」，先进先出。

    ``emotion`` 交给 ``persona.tag_emotion`` 编码进字符串末尾——
    ``User.memory`` 是 ``list[str]``，没有 metadata 可放数值。
    「今天被说成机器人」这种事本身就是高情绪的，不该按 0 存。
    """
    body = (text or "").strip()
    if not body:
        return False
    try:
        from .memory.persona import tag_emotion

        row = _read_self_row()
        row.memory.append(tag_emotion(body, emotion))
        overflow = len(row.memory) - limit
        if overflow > 0:
            del row.memory[:overflow]
        user_table.update(
            (("DATA",), (row.model_dump_json(),)), f"ID='{SELF_MEMORY_ID}'"
        )
        return True
    except Exception:
        return False
