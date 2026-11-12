import re
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


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

DEFAULT_MAX_READ_CHARS = 4000
"""单次读取的默认字符上限"""

MAX_SKILL_READ_CHARS = 8000
"""单次读取的硬上限：防止模型用 limit=999999 把上下文打爆"""

_SECTION_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$")
_TITLE_NOISE_RE = re.compile(r"[\s#:：、，,。.！!？?（）()\[\]【】\-_/]+")


def normalize_section_title(title: str) -> str:
    """章节名归一：忽略 #、空白、大小写与中英文标点差异。"""
    return _TITLE_NOISE_RE.sub("", str(title or "")).lower()


class SkillSection(BaseModel):
    """技能正文里的一个二/三级章节。"""

    title: str
    level: int
    start: int
    """章节起始字符位置（含标题行）"""
    end: int
    """章节结束字符位置（不含下一同级/更高级标题）"""


def compute_sections(content: str) -> list[SkillSection]:
    """扫描 `##` / `###` 标题，返回章节位置。

    代码块里的 `#` 不算标题；章节范围包含其子章节（下一个同级或更高级
    标题才是边界）。
    """
    headings: list[tuple[int, str, int, int]] = []
    offset = 0
    in_fence = False
    for line in (content or "").splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
        elif not in_fence:
            match = _SECTION_RE.match(line.rstrip("\r\n"))
            if match:
                headings.append(
                    (len(match.group(1)), match.group(2), offset, offset + len(line))
                )
        offset += len(line)

    sections: list[SkillSection] = []
    for index, (level, title, start, _) in enumerate(headings):
        end = len(content or "")
        for next_level, _, next_start, _ in headings[index + 1 :]:
            if next_level <= level:
                end = next_start
                break
        sections.append(SkillSection(title=title, level=level, start=start, end=end))
    return sections


class Skill(BaseModel):
    name: str
    """技能标识 = 文件名主干"""

    description: str = ""
    content: str = ""
    source: SkillSource = SkillSource.BUILTIN
    enabled: bool = True
    embedded: bool = False

    when_to_use: str = ""
    """什么时候该读这篇；进入技能索引，帮助模型判断要不要展开"""

    tags: list[str] = Field(default_factory=list)
    """分类标签，UI 过滤用"""

    priority: int = 0
    """索引排序：越大越靠前（内置技能默认 0，玩法类可设 50）"""

    embed: bool | None = None
    """frontmatter 声明的内嵌覆盖；None = 由 AgentConfig.skills_embed 决定"""

    max_read_chars: int = DEFAULT_MAX_READ_CHARS
    """该技能单次读取上限"""

    sections: list[SkillSection] = Field(default_factory=list)
    """正文章节位置（构建/替换正文时自动重算）"""

    @model_validator(mode="after")
    def _fill_sections(self):
        # 只有正文与缓存不一致时才重算，避免每次 model_copy 都扫一遍。
        expected = compute_sections(self.content)
        if self.sections != expected:
            self.sections = expected
        return self

    @property
    def source_label(self) -> str:
        return SKILL_SOURCE_LABEL.get(self.source, str(self.source))

    @property
    def deletable(self) -> bool:
        """仅内置技能不可删除（编辑内置会创建同名用户配置，此后可删）。

        `virtual` 技能不由技能页管理，其生命周期跟随所属功能插件。
        """
        return self.source is not SkillSource.BUILTIN

    def find_section(self, query: str) -> SkillSection | None:
        """按标题或标题片段找章节；先精确匹配，再按包含关系匹配。"""
        key = normalize_section_title(query)
        if not key:
            return None
        for section in self.sections:
            if normalize_section_title(section.title) == key:
                return section
        for section in self.sections:
            title = normalize_section_title(section.title)
            if key in title or (title and title in key):
                return section
        return None

    def section_titles(self) -> list[str]:
        return [section.title for section in self.sections]


def _first_line(content: str) -> str:
    """从正文首个非标题行截取描述（description 缺失时的兜底）。"""
    for line in (content or "").splitlines():
        text = line.strip().lstrip("#").strip()
        if text:
            return text[:MAX_SKILL_DESCRIPTION_LENGTH]
    return ""


def _parse_bool(value) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "是", "on"}:
        return True
    if text in {"false", "0", "no", "否", "off"}:
        return False
    return None


def _parse_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_tags(value) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list | tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


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
