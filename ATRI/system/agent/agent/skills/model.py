from enum import StrEnum

from pydantic import BaseModel


class SkillSource(StrEnum):
    BUILTIN = "builtin"
    """内置：仅存在内置文件，只读"""

    USER = "user"
    """用户：仅存在用户文件"""

    MODIFIED = "modified"
    """内置（已修改）：内置文件 + 同名用户文件，以用户文件为准"""

    VIRTUAL = "virtual"
    """插件提供：正文由功能插件在注册时注入内存，**不落盘**"""


SKILL_SOURCE_LABEL: dict[SkillSource, str] = {
    SkillSource.BUILTIN: "内置",
    SkillSource.USER: "用户",
    SkillSource.MODIFIED: "内置（已修改）",
    SkillSource.VIRTUAL: "插件提供",
}

MAX_SKILL_NAME_LENGTH = 64
"""技能名（文件名主干）长度上限"""

MAX_SKILL_DESCRIPTION_LENGTH = 80
"""description 缺失时从正文首行回退截断的长度"""

INVALID_SKILL_NAME_CHARS = '\\/:*?"<>|'


class Skill(BaseModel):
    name: str
    """技能标识 = 文件名主干"""

    description: str = ""
    content: str = ""
    source: SkillSource = SkillSource.BUILTIN
    enabled: bool = True
    embedded: bool = False

    @property
    def source_label(self) -> str:
        return SKILL_SOURCE_LABEL.get(self.source, str(self.source))

    @property
    def deletable(self) -> bool:
        """仅内置技能不可删除（编辑内置会创建同名用户配置，此后可删）。

        `virtual` 技能不由技能页管理，其生命周期跟随所属功能插件。
        """
        return self.source is not SkillSource.BUILTIN


def _first_line(content: str) -> str:
    """从正文首个非标题行截取描述（description 缺失时的兜底）。"""
    for line in (content or "").splitlines():
        text = line.strip().lstrip("#").strip()
        if text:
            return text[:MAX_SKILL_DESCRIPTION_LENGTH]
    return ""


def validate_skill_name(name: str) -> str | None:
    """返回错误信息；合法则返回 None。"""
    if not name:
        return "技能名不能为空"
    if len(name) > MAX_SKILL_NAME_LENGTH:
        return f"技能名长度不能超过 {MAX_SKILL_NAME_LENGTH} 个字符"
    if name.startswith("."):
        return "技能名不能以 . 开头"
    for char in INVALID_SKILL_NAME_CHARS:
        if char in name:
            return f"技能名不能包含字符 {char}"
    if name.strip() != name:
        return "技能名首尾不能有空白字符"
    return None
