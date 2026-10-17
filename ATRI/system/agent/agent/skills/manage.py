import yaml

from ATRI.log import log

from ...config import config
from ...service import plugin
from .loader import USER_SKILLS_SUBDIR, scan_merged
from .model import Skill, SkillSource, validate_skill_name


class SkillManager:
    """技能系统对外唯一入口（单例 skill_manager）。

    - 内容：内置目录只读，用户目录可写；同名用户文件覆盖内置。
    - 开关：`AgentConfig.skills_disable`（禁用列表）/ `skills_embed`（内嵌列表），
      两者都是"记录例外"，按技能名记录，与正文来源目录无关。
    """

    def __init__(self, user_dir=None):
        self._user_dir = user_dir
        self._cache: dict[str, Skill] | None = None

    # ---- 目录 ----

    @property
    def user_dir(self):
        if self._user_dir is None:
            self._user_dir = plugin.get_path() / USER_SKILLS_SUBDIR
        return self._user_dir

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

    def refresh(self):
        self._cache = None

    def _all(self) -> dict[str, Skill]:
        if self._cache is None:
            self._cache = scan_merged(self.user_dir)
        return self._cache

    def _decorate(self, skill: Skill) -> Skill:
        name = skill.name
        skill.enabled = name not in self._read_list("skills_disable")
        skill.embedded = name in self._read_list("skills_embed")
        return skill

    # ---- 对外 API ----

    def list_all(self) -> list[Skill]:
        """合并视图全量（UI 用，含未启用）"""
        return [self._decorate(s.model_copy(deep=True)) for s in self._all().values()]

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
        self.refresh()
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
        if not path.is_file() or skill is None or skill.source is SkillSource.BUILTIN:
            raise ValueError("该技能不是用户技能，无法删除")
        was_overriding_builtin = skill.source is SkillSource.MODIFIED
        path.unlink()
        self.refresh()
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
