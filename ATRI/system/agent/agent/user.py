from pydantic import Field, model_validator

from ATRI.permission import get_master_ids
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

    alias: list = Field(default_factory=list)
    """别名：外号、习惯称呼。按 id / 名字找人时一起匹配。"""

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


#: 认识的人最多列出几个。太多会挤占提示词，且大部分她根本不在意。
#: 名单是给人看的，10 个够用；再长的名单没人读完，只是白烧 token。
KNOWN_USERS_LIMIT = 10

#: 一个人最多记几个别名。别名的用途是「听得懂别人怎么称呼他」，几个就够，
#: 多了只会让找人的匹配变模糊。
ALIAS_LIMIT = 8

#: 单个别名的最大长度。
ALIAS_MAX_LEN = 20


def normalize_alias(text: str) -> str:
    """把别名收拾成可比较的形式：去首尾空白、压缩内部空白、按长度截断。"""
    return " ".join(str(text or "").split())[:ALIAS_MAX_LEN]


def get_aliases(user_id: str) -> list[str]:
    """取某个用户已经记下的别名。"""
    try:
        info = get_user_info(user_id)
    except Exception:
        return []
    return [normalize_alias(a) for a in (info.alias or []) if normalize_alias(a)]


def add_alias(user_id: str, alias: str) -> tuple[bool, str]:
    """给某个用户记一个别名，返回 ``(是否成功, 说明)``。

    大小写不敏感去重（``Kiana`` 和 ``kiana`` 是同一个称呼）；满了直接失败，
    不静默丢弃——调用方需要能告诉用户「记不下更多了」。
    """
    name = normalize_alias(alias)
    if not name:
        return False, "别名是空的"
    try:
        info = get_user_info(user_id)
    except Exception as exc:
        return False, f"读取用户信息失败：{exc}"
    aliases = [normalize_alias(a) for a in (info.alias or []) if normalize_alias(a)]
    if any(a.casefold() == name.casefold() for a in aliases):
        return False, f"「{name}」已经记过了"
    if len(aliases) >= ALIAS_LIMIT:
        return False, f"别名最多记 {ALIAS_LIMIT} 个，先删掉几个再记"
    aliases.append(name)
    info.alias = aliases
    try:
        save_user_info(user_id, info)
    except Exception as exc:
        return False, f"保存失败：{exc}"
    return True, f"已记下「{name}」"


def remove_alias(user_id: str, alias: str) -> tuple[bool, str]:
    """去掉一个别名（大小写不敏感），返回 ``(是否成功, 说明)``。"""
    name = normalize_alias(alias)
    if not name:
        return False, "别名是空的"
    try:
        info = get_user_info(user_id)
    except Exception as exc:
        return False, f"读取用户信息失败：{exc}"
    aliases = [normalize_alias(a) for a in (info.alias or []) if normalize_alias(a)]
    kept = [a for a in aliases if a.casefold() != name.casefold()]
    if len(kept) == len(aliases):
        return False, f"没有记过「{name}」这个别名"
    info.alias = kept
    try:
        save_user_info(user_id, info)
    except Exception as exc:
        return False, f"保存失败：{exc}"
    return True, f"已忘掉「{name}」"


def _nickname(user_id: str) -> str:
    """lkbot 里登记的昵称；没有就空串。"""
    try:
        from ATRI.system.lkapi.bot import util as lk_util

        if lk_util.is_valid_user(user_id):
            return (lk_util.get_name(user_id) or "").strip()
    except Exception:
        pass
    return ""


def _is_master(user_id: str) -> bool:
    try:
        from .util import get_user_group

        return get_user_group(user_id) == "主人"
    except Exception:
        return False


def list_known_users(limit: int = KNOWN_USERS_LIMIT) -> list[dict]:
    """列出她记录过的用户（按熟络度从高到低）。

    供「你主人是谁」「你和某某关系怎样」这类问题使用：
    提示词里只有**当前对话人**的关系，别人问起第三者时她没有依据。

    每条带上 ``nickname`` 与 ``alias``：找人时名字、别名都认，不用再查一遍库。
    """
    try:
        rows = user_table.select_all()
    except Exception:
        return []

    users = []
    for row in rows:
        uid = str(row[0])
        if uid.startswith("__"):
            continue
        try:
            info = User.model_validate_json(row[1])
        except Exception:
            continue
        users.append(
            {
                "user_id": uid,
                "nickname": _nickname(uid),
                "alias": [
                    normalize_alias(a) for a in (info.alias or []) if normalize_alias(a)
                ],
                "familiarity": float(info.familiarity or 0.0),
                "love": int(info.love or 0),
                "mood_tag": (info.mood_tag or "").strip(),
                "profile": (info.profile or "").strip(),
                "relation": describe_relation(info),
                "is_master": _is_master(uid),
            }
        )
    users.sort(key=lambda u: u["familiarity"], reverse=True)
    return users[:limit]


def describe_masters() -> str:
    """把「主人是谁」渲染成人话，供关系查询直接回答。"""
    ids = get_master_ids()
    if not ids:
        return "现在没有登记主人。"

    blocks = []
    for uid in ids:
        found = find_user(uid) or {}
        nickname = str(found.get("nickname") or _nickname(uid) or "").strip()
        lines = [f"主人：{uid}" + (f"（{nickname}）" if nickname else "")]
        alias = found.get("alias") or []
        if alias:
            lines.append("别名：" + "、".join(alias))
        if found.get("relation"):
            lines.append(f"你和TA的关系：{found['relation']}")
        if found.get("profile"):
            lines.append(f"你对TA的印象：{found['profile']}")
        if not found and not nickname:
            lines.append("（还没跟TA说过话，只知道号码）")
        blocks.append("\n".join(lines))

    if len(blocks) == 1:
        return blocks[0]
    return f"你的主人有 {len(blocks)} 位：\n" + "\n\n".join(blocks)


def find_user(query: str) -> dict | None:
    """按 **QQ 号 / 别名 / 名字** 找一个人。

    匹配顺序从确定到模糊：

    1. 完全等于 ``user_id``；
    2. 完全等于某个别名（大小写不敏感）；
    3. 完全等于 lkbot 里登记的昵称；
    4. ``user_id`` 包含查询串（记得半个 QQ 号也找不到？这里兜一下）；
    5. 昵称、别名或画像里包含查询串。

    找不到返回 None——调用方应如实说「不太记得这个人」，而不是编一个出来。
    """
    keyword = str(query or "").strip()
    if not keyword:
        return None
    folded = keyword.casefold()
    users = list_known_users(limit=200)

    for u in users:
        if u["user_id"] == keyword:
            return u
    for u in users:
        if any(a.casefold() == folded for a in u["alias"]):
            return u
    for u in users:
        if u["nickname"] and u["nickname"].casefold() == folded:
            return u
    for u in users:
        if keyword in u["user_id"]:
            return u
    for u in users:
        if any(folded in a.casefold() for a in u["alias"]):
            return u
    for u in users:
        if u["nickname"] and folded in u["nickname"].casefold():
            return u
    for u in users:
        if keyword in u["profile"]:
            return u
    return None


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
