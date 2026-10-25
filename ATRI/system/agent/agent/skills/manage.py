import yaml

from ATRI.log import log

from ...config import config
from ...service import plugin
from .loader import USER_SKILLS_SUBDIR, scan_merged
from .model import Skill, SkillSource, _first_line, validate_skill_name


class SkillManager:
    """技能系统对外唯一入口（单例 skill_manager）。

    - 内容：内置目录只读，用户目录可写；同名用户文件覆盖内置。
    - 开关：`AgentConfig.skills_disable`（禁用列表）/ `skills_embed`（内嵌列表），
      两者都是"记录例外"，按技能名记录，与正文来源目录无关。
    - 内存技能：功能插件自带的正文**不落盘**，由 `put_virtual()` 注入内存，
      与磁盘扫描结果合并后对外表现一致（卸载时 `drop_virtual()` 摘除）。
    """

    def __init__(self, user_dir=None):
        self._user_dir = user_dir
        self._cache: dict[str, Skill] | None = None
        self._virtual: dict[str, Skill] = {}

    # ---- 目录 ----

    @property
    def user_dir(self):
        if self._user_dir is None:
            self._user_dir = plugin.get_path() / USER_SKILLS_SUBDIR
        return self._user_dir

    def put_virtual(self, name: str, description: str, content: str) -> Skill:
        """注入一个只存在于内存的技能；同名磁盘技能会被它覆盖。"""
        error = validate_skill_name(name)
        if error:
            raise ValueError(error)
        skill = Skill(
            name=name,
            description=description or _first_line(content),
            content=content,
            source=SkillSource.VIRTUAL,
        )
        self._virtual[name] = skill
        self._cache = None
        return self._decorate(skill.model_copy(deep=True))

    def drop_virtual(self, name: str) -> bool:
        """摘除内存技能；同时清掉它的开关记录，避免残留"幽灵开关"。"""
        removed = self._virtual.pop(name, None) is not None
        if removed:
            self._cache = None
            self._drop_from_toggles(name)
        return removed

    def clear_virtual(self):
        """清空全部内存技能（连同开关记录）。"""
        for name in list(self._virtual):
            self.drop_virtual(name)

    def is_virtual(self, name: str) -> bool:
        return name in self._virtual

    # ---- 开关（AgentConfig 两个列表）----

    @staticmethod
    def _read_list(key: str) -> list[str]:
        return list(config.get(key) or [])

    @classmethod
    def _write_toggle(cls, key: str, name: str, on: bool):
        # config.get() 返回内部引用，必须构造新列表后 config.set 才会落盘
        names = cls._read_list(key)
        if on:
            if name not in names:
                names.append(name)
        elif name in names:
            names.remove(name)
        config.set(key, names)

    @staticmethod
    def _drop_from_toggles(name: str):
        """删除纯用户技能时，同时清理两个开关列表中的该名字。"""
        for key in ("skills_disable", "skills_embed"):
            names = list(config.get(key) or [])
            if name in names:
                names.remove(name)
                config.set(key, names)

    # ---- 数据 ----

    def invalidate(self):
        """丢弃磁盘扫描缓存；不动功能插件注入的内存技能。

        写文件（save_user / delete_user）后、以及外部改动技能目录后调用它即可。
        插件内存技能的生命周期由插件自己负责（注册注入、卸载摘除），此处不介入。
        """
        self._cache = None

    def _all(self) -> dict[str, Skill]:
        if self._cache is None:
            merged = scan_merged(self.user_dir)
            merged.update(self._virtual)
            self._cache = merged
        return self._cache

    def _decorate(self, skill: Skill) -> Skill:
        name = skill.name
        plugin_enabled = self._plugin_enabled(name)
        skill.enabled = plugin_enabled and name not in self._read_list("skills_disable")
        skill.embedded = plugin_enabled and name in self._read_list("skills_embed")
        return skill

    @staticmethod
    def _plugin_enabled(skill_name: str) -> bool:
        """技能所属功能插件的整体开关；不属于任何插件时恒为 True。"""
        from ..function_plugin.manage import function_plugin_manager

        return function_plugin_manager.is_enabled(
            function_plugin_manager.owner_of_skill(skill_name)
        )

    # ---- 对外 API ----

    def list_all(self) -> list[Skill]:
        """合并视图全量（UI 用，含未启用）"""
        return [self._decorate(s.model_copy(deep=True)) for s in self._all().values()]

    def list_standalone(self) -> list[Skill]:
        """不属于任何功能插件的技能（技能页只展示这些）。"""
        from ..function_plugin.manage import function_plugin_manager

        return [
            s
            for s in self.list_all()
            if function_plugin_manager.owner_of_skill(s.name) is None
        ]

    def list_queryable(self) -> list[Skill]:
        """enabled ∧ not embedded（list_skills FC 用）"""
        return [s for s in self.list_all() if s.enabled and not s.embedded]

    def get(self, name: str) -> Skill | None:
        """生效版本（用户覆盖优先）"""
        skill = self._all().get(name)
        if skill is None:
            return None
        return self._decorate(skill.model_copy(deep=True))

    def get_embedded_prompt(self) -> str:
        """enabled ∧ embedded 的正文拼接"""
        return "\n\n".join(
            s.content.strip()
            for s in self.list_all()
            if s.enabled and s.embedded and s.content.strip()
        )

    def save_user(self, name: str, description: str, content: str) -> Skill:
        """只写用户目录；同名内置自动成为覆盖（内置（已修改））。"""
        error = validate_skill_name(name)
        if error:
            raise ValueError(error)
        self.user_dir.mkdir(parents=True, exist_ok=True)
        path = self.user_dir / f"{name}.md"
        text = (content or "").strip("\n")
        if description.strip():
            frontmatter = yaml.safe_dump(
                {"description": description},
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )
            document = f"---\n{frontmatter}---\n\n{text}\n"
        else:
            document = f"{text}\n"
        path.write_text(document, encoding="utf-8")
        self.invalidate()
        return self.get(name) or Skill(name=name, source=SkillSource.USER)

    def delete_user(self, name: str) -> bool:
        """只删用户文件；纯内置 → 拒绝（不触碰 res/）。

        覆盖内置的技能被删除后恢复内置，并保留该名字在开关列表中的状态；
        纯用户技能被删除时，同时清理两个开关列表中的该名字。
        """
        error = validate_skill_name(name)
        if error:
            raise ValueError(error)
        path = self.user_dir / f"{name}.md"
        skill = self._all().get(name)
        if skill is not None and skill.source is SkillSource.VIRTUAL:
            raise ValueError("该技能由功能插件提供，不能在此删除")
        if not path.is_file() or skill is None or skill.source is SkillSource.BUILTIN:
            raise ValueError("该技能不是用户技能，无法删除")
        was_overriding_builtin = skill.source is SkillSource.MODIFIED
        path.unlink()
        self.invalidate()
        if not was_overriding_builtin:
            self._drop_from_toggles(name)
        return True

    def set_enabled(self, name: str, enabled: bool):
        if name not in self._all():
            raise ValueError("技能不存在")
        self._write_toggle("skills_disable", name, not enabled)
        return self.get(name)

    def set_embedded(self, name: str, embedded: bool):
        if name not in self._all():
            raise ValueError("技能不存在")
        self._write_toggle("skills_embed", name, embedded)
        return self.get(name)


skill_manager = SkillManager()


def get_skill_manager() -> SkillManager:
    return skill_manager


def embedded_prompt() -> str:
    """thinking.get_prompt 使用；技能目录不存在时返回空串。"""
    try:
        return skill_manager.get_embedded_prompt()
    except Exception as e:
        log.warning(f"内嵌技能提示词生成失败:{e}")
        return ""
