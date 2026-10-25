from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

MAX_PLUGIN_NAME_LENGTH = 64
"""功能插件名长度上限"""

INVALID_PLUGIN_NAME_CHARS = '\\/:*?"<>|'


class PluginSkill(BaseModel):
    """插件自带的技能正文（内联在声明里，注册时注入内存，不落盘）。"""

    name: str
    """技能名（= 文件名主干）"""

    content: str = ""
    """Markdown 正文"""

    description: str = ""
    """一句话说明；留空时由注入逻辑从正文首行回退"""


class PluginSkillFile(BaseModel):
    """插件自带的技能正文（来自插件自有目录下的 md 文件）。"""

    path: Path
    """md 文件路径；相对路径按技能系统的工作目录解析"""

    name: str = ""
    """技能名；留空时取文件名主干"""


class FunctionPlugin(BaseModel):
    """一个功能插件（声明 + 注册时可执行的登记动作）。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    """插件标识，开关状态按它记录"""

    description: str = ""
    """一句话说明，显示在 WebUI 与不常用功能清单里"""

    skills: list[Any] = Field(default_factory=list)
    """技能：`str`（引用通用技能目录里的既有技能）或 `PluginSkill`（内联正文）"""

    skill_files: list[Any] = Field(default_factory=list)
    """插件自有目录下的技能文件：`PluginSkillFile` 或路径字符串。

    这些文件**不在**通用技能目录里，因此不会被技能系统自动扫描；
    插件注册时把它们读进内存，卸载时随之消失。
    """

    functions: list[Any] = Field(default_factory=list)
    """常用功能：`str`（引用已注册功能）或 `FunctionCalling` 子类"""

    rare_functions: list[Any] = Field(default_factory=list)
    """不常用功能：同上，但不进提示词，只能由 call 调用"""

    enabled: bool = True
    """整体开关状态（查询时按配置填充，不参与持久化）"""

    bundled_skills: list[PluginSkill] = Field(default_factory=list)
    """注册时由插件注入内存的技能正文（含从 skill_files 读到的内容）"""

    bundled_function_names: list[str] = Field(default_factory=list)
    """注册时由插件登记进 FunctionCallingManager 的功能名（卸载时会被摘除）"""

    @property
    def all_functions(self) -> list[str]:
        return [self._func_name(f) for f in [*self.functions, *self.rare_functions]]

    @property
    def skill_names(self) -> list[str]:
        names = [s.name if isinstance(s, PluginSkill) else str(s) for s in self.skills]
        names.extend(self._skill_file_name(f) for f in self.skill_files)
        return names

    @property
    def bundled_skill_objects(self) -> list[PluginSkill]:
        """插件自带正文的技能：内联的 + 从自有目录读到的。"""
        inline = [s for s in self.skills if isinstance(s, PluginSkill)]
        return inline + self._load_skill_files()

    @property
    def _declared_skill_files(self) -> list[PluginSkillFile]:
        files: list[PluginSkillFile] = []
        for item in self.skill_files:
            if isinstance(item, PluginSkillFile):
                files.append(item)
            else:
                files.append(PluginSkillFile(path=Path(str(item))))
        return files

    def _load_skill_files(self) -> list[PluginSkill]:
        """把 skill_files 读成 PluginSkill；文件缺失只记 warning，不阻断注册。"""
        loaded: list[PluginSkill] = []
        for item in self._declared_skill_files:
            path = item.path
            if not path.is_absolute():
                path = Path.cwd() / path
            name = item.name or path.stem
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as e:
                from ATRI.log import log

                log.warning(f"功能插件 {self.name} 的技能文件 {path} 读取失败:{e}")
                continue
            from ..skills.loader import parse_skill_text

            meta, content = parse_skill_text(text)
            loaded.append(
                PluginSkill(
                    name=name,
                    content=content,
                    description=str(meta.get("description") or "").strip(),
                )
            )
        return loaded

    @staticmethod
    def _skill_file_name(item: Any) -> str:
        if isinstance(item, PluginSkillFile):
            return item.name or item.path.stem
        return Path(str(item)).stem

    @property
    def bundled_functions(self) -> list[Any]:
        """插件自带实现的功能类（注册时由插件登记）。"""
        return [
            f for f in [*self.functions, *self.rare_functions] if not isinstance(f, str)
        ]

    @staticmethod
    def _func_name(item: Any) -> str:
        if isinstance(item, str):
            return item
        name = getattr(item, "function_name", "")
        return str(name or type(item).__name__)

    def normalized(self) -> "FunctionPlugin":
        """纯名字视图：自带内容解析成名字，便于持久展示与归属查询。

        同时记下"哪些是插件自己带进来的"（`bundled_skill_names` /
        `bundled_function_names`），卸载时据此完全撤回，不会误删引用来的内容。
        """
        clone = self.model_copy(deep=True)
        clone.bundled_skills = [
            s.model_copy(deep=True) for s in self.bundled_skill_objects
        ]
        clone.bundled_function_names = [
            self._func_name(f) for f in self.bundled_functions
        ]
        clone.skills = list(self.skill_names)
        clone.skill_files = []
        clone.functions = [self._func_name(f) for f in self.functions]
        clone.rare_functions = [self._func_name(f) for f in self.rare_functions]
        return clone


def validate_plugin_name(name: str) -> str | None:
    """返回错误信息；合法则返回 None。"""
    if not name:
        return "功能插件名不能为空"
    if len(name) > MAX_PLUGIN_NAME_LENGTH:
        return f"功能插件名长度不能超过 {MAX_PLUGIN_NAME_LENGTH} 个字符"
    if name.startswith("."):
        return "功能插件名不能以 . 开头"
    for char in INVALID_PLUGIN_NAME_CHARS:
        if char in name:
            return f"功能插件名不能包含字符 {char}"
    if name.strip() != name:
        return "功能插件名首尾不能有空白字符"
    return None
