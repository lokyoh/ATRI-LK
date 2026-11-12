import yaml

from ATRI.log import log

from ...config import config
from ...service import plugin
from .loader import USER_SKILLS_SUBDIR, scan_merged
from .model import (
    DEFAULT_MAX_READ_CHARS,
    MAX_SKILL_READ_CHARS,
    Skill,
    SkillSource,
    _first_line,
    validate_skill_name,
)

#: 技能索引在提示词里的字符预算（约 400 token）
SKILL_INDEX_BUDGET = 1200

#: 所有内嵌技能正文合计的字符预算
SKILL_EMBEDDED_BUDGET = 8000

#: 单个内嵌技能正文的默认截断长度
EMBEDDED_SKILL_CHARS = 4000

#: 技能目录 + 内嵌正文的总字符预算
SKILL_PROMPT_BUDGET = 10000


class SkillManager:
    """技能系统对外唯一入口（单例 skill_manager）。

    - 内容：内置目录只读，用户目录可写；同名用户文件覆盖内置。
    - 开关：`AgentConfig.skills_disable`（禁用列表）/ `skills_embed`（内嵌列表），
      两者都是"记录例外"，按技能名记录，与正文来源目录无关；
      frontmatter 的 `embed` 可覆盖内嵌配置。
    - 内存技能：功能插件自带的正文**不落盘**，由 `put_virtual()` 注入内存，
      与磁盘扫描结果合并后对外表现一致（卸载时 `drop_virtual()` 摘除）。
    """

    def __init__(self, user_dir=None):
        self._user_dir = user_dir
        self._cache: dict[str, Skill] | None = None
        self._virtual: dict[str, Skill] = {}
        self._version = 0
        self._index_cache: tuple[tuple, int, str] | None = None
        """(key, budget, rendered) 缓存；只改配置/文件时失效。"""

    # ---- 目录 ----

    @property
    def user_dir(self):
        if self._user_dir is None:
            self._user_dir = plugin.get_path() / USER_SKILLS_SUBDIR
        return self._user_dir

    def put_virtual(
        self,
        name: str,
        description: str,
        content: str,
        *,
        when_to_use: str = "",
        tags: list[str] | None = None,
        priority: int = 0,
        embed: bool | None = None,
        max_read_chars: int = DEFAULT_MAX_READ_CHARS,
    ) -> Skill:
        """注入一个只存在于内存的技能；同名磁盘技能会被它覆盖。"""
        error = validate_skill_name(name)
        if error:
            raise ValueError(error)
        skill = Skill(
            name=name,
            description=description or _first_line(content),
            content=content,
            source=SkillSource.VIRTUAL,
            when_to_use=str(when_to_use or "").strip(),
            tags=list(tags or []),
            priority=int(priority or 0),
            embed=embed,
            max_read_chars=int(max_read_chars or DEFAULT_MAX_READ_CHARS),
        )
        self._virtual[name] = skill
        self._touch()
        return self._decorate(skill.model_copy(deep=True))

    def drop_virtual(self, name: str) -> bool:
        """摘除内存技能；同时清掉它的开关记录，避免残留"幽灵开关"。"""
        removed = self._virtual.pop(name, None) is not None
        if removed:
            self._touch()
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

    def _write_toggle(self, key: str, name: str, on: bool):
        # config.get() 返回内部引用，必须构造新列表后 config.set 才会落盘
        names = self._read_list(key)
        if on:
            if name not in names:
                names.append(name)
        elif name in names:
            names.remove(name)
        config.set(key, names)
        # 开关影响索引与内嵌正文，派生缓存必须一起失效。
        self._touch()

    def _drop_from_toggles(self, name: str):
        """删除纯用户技能时，同时清理两个开关列表中的该名字。"""
        for key in ("skills_disable", "skills_embed"):
            names = list(config.get(key) or [])
            if name in names:
                names.remove(name)
                config.set(key, names)
        self._touch()

    # ---- 数据 ----

    def _touch(self):
        """内容或开关变化：让派生缓存一起失效。"""
        self._cache = None
        self._version += 1
        self._index_cache = None

    def invalidate(self):
        """丢弃磁盘扫描缓存；不动功能插件注入的内存技能。

        写文件（save_user / delete_user）后、以及外部改动技能目录后调用它即可。
        插件内存技能的生命周期由插件自己负责（注册注入、卸载摘除），此处不介入。
        """
        self._touch()

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
        if skill.embed is not None:
            # frontmatter 显式声明的 embed 覆盖配置开关。
            skill.embedded = plugin_enabled and bool(skill.embed)
        else:
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
        """enabled ∧ not embedded（技能查询与索引用）"""
        return [s for s in self.list_all() if s.enabled and not s.embedded]

    def list_index(self, budget: int = SKILL_INDEX_BUDGET) -> str:
        """提示词里的技能目录：名称 + 来源 + 说明（+ 何时该读）。

        超出预算时保留高优先级技能，末尾提示用 `skills()` 查完整清单。
        结果按（内容版本, 开关列表, 预算）缓存，避免每轮重复拼接。
        """
        skills = sorted(
            self.list_queryable(),
            key=lambda s: (-int(s.priority or 0), s.name),
        )
        if not skills:
            return ""
        key = (
            self._version,
            budget,
            tuple(self._read_list("skills_disable")),
            tuple(self._read_list("skills_embed")),
            tuple(config.get("function_plugin_disable") or []),
        )
        cached = self._index_cache
        if cached is not None and cached[0] == key:
            return cached[2]

        lines: list[str] = []
        used = 0
        listed = 0
        for skill in skills:
            description = (skill.description or "无说明")[:60]
            line = f"- {skill.name} [{skill.source_label}]：{description}"
            extra = 0
            if skill.when_to_use:
                extra = len(skill.when_to_use) + 3
            if listed and used + len(line) + extra > budget:
                break
            lines.append(line)
            if skill.when_to_use:
                lines.append(f"  何时读：{skill.when_to_use}")
            used += len(line) + extra
            listed += 1

        remaining = len(skills) - listed
        if remaining > 0:
            lines.append(f"还有 {remaining} 个技能未列出，用 skills() 查看完整清单。")
        rendered = "\n".join(lines)
        self._index_cache = (key, budget, rendered)
        return rendered

    def get(self, name: str) -> Skill | None:
        """生效版本（用户覆盖优先）"""
        skill = self._all().get(name)
        if skill is None:
            return None
        return self._decorate(skill.model_copy(deep=True))

    def get_embedded_prompt(self, budget: int = SKILL_EMBEDDED_BUDGET) -> str:
        """enabled ∧ embedded 的正文拼接；单项与总量都受预算约束。"""
        parts: list[str] = []
        used = 0
        truncated = False
        for skill in self.list_all():
            if not skill.enabled or not skill.embedded:
                continue
            body = skill.content.strip()
            if not body:
                continue
            per_skill = min(
                EMBEDDED_SKILL_CHARS,
                max(1, int(skill.max_read_chars or DEFAULT_MAX_READ_CHARS)),
                budget - used,
            )
            if per_skill <= 0:
                truncated = True
                break
            if len(body) > per_skill:
                body = body[:per_skill]
                truncated = True
            parts.append(body)
            used += len(body)
        if truncated:
            log.warning(
                f"内嵌技能正文超过预算 {budget} 字，已截断；请精简或改用索引导读"
            )
        return "\n\n".join(parts)

    def get_prompt_block(self, budget: int = SKILL_PROMPT_BUDGET) -> str:
        """技能目录 + 内嵌正文的完整提示词片段（含 `#你的技能` 标题）。

        总预算先给索引、再给内嵌正文：索引是「有哪些技能」的入口，
        比某一篇正文更重要。为空时返回空串，调用方不要输出空占位。
        """
        index = self.list_index(min(SKILL_INDEX_BUDGET, max(1, budget)))
        remaining = max(0, budget - len(index))
        embedded = self.get_embedded_prompt(remaining) if remaining else ""
        if not index and not embedded:
            return ""
        parts = [
            '#你的技能\n需要细节时用 skill(name="技能名", section="章节名") 读取。'
        ]
        if index:
            parts.append(index)
        else:
            parts.append("（技能目录为空）")
        if embedded:
            parts.append("\n#直接写入提示词的技能正文\n" + embedded)
        result = "\n".join(parts) + "\n\n"
        try:
            from ..telemetry import telemetry

            telemetry.record(
                "skill_index", chars=len(index), embedded_chars=len(embedded)
            )
        except Exception:
            pass
        return result

    def read(
        self,
        skill: Skill,
        section: str = "",
        offset: int = 0,
        limit: int = 0,
    ) -> str:
        """按章节或偏移读取技能正文，返回可直接回填给模型的文本。

        - 指定 ``section``：命中返回该章节（含子章节）；未命中返回章节列表。
        - 未指定：从 ``offset`` 起截取 ``limit`` 字，附章节列表与续读提示。
        """
        cap = min(
            MAX_SKILL_READ_CHARS,
            max(1, int(skill.max_read_chars or DEFAULT_MAX_READ_CHARS)),
        )
        if limit <= 0:
            limit = cap
        limit = min(int(limit), cap)
        offset = max(0, int(offset or 0))

        title = f"# {skill.name}"
        if skill.description:
            title += f"\n{skill.description}"
        sections = skill.section_titles()

        if section:
            match = skill.find_section(section)
            if match is not None:
                body = skill.content[match.start : match.end].strip()
                if len(body) > limit:
                    body = body[:limit] + "\n…（章节过长，已截断）"
                return f"{title}\n\n{body}"
            chapter_list = "、".join(sections) or "（无章节）"
            return (
                f"{title}\n\n章节「{section}」不存在。"
                f"可用章节：{chapter_list}\n"
                "不带 section 可读取全文。"
            )

        body = skill.content[offset:]
        if len(body) > limit:
            body = body[:limit]
            next_offset = offset + len(body)
            tail = (
                f"\n\n[已读 {next_offset}/{len(skill.content)} 字；"
                f'继续读：skill(name="{skill.name}", offset={next_offset})]'
            )
        else:
            tail = ""
        head = title
        if sections:
            head += "\n\n可用章节（可用 section 精确读取）：" + "、".join(sections)
        return f"{head}\n\n{body}{tail}"

    def save_user(
        self,
        name: str,
        description: str,
        content: str,
        *,
        when_to_use: str | None = None,
        tags: list[str] | None = None,
        priority: int | None = None,
        embed: bool | None = None,
        max_read_chars: int | None = None,
    ) -> Skill:
        """只写用户目录；同名内置自动成为覆盖（内置（已修改））。

        只写入显式提供的元数据字段；未提供的保持当前生效值，
        避免 WebUI 编辑一下就把 frontmatter 里的优先级/何时读清空。
        """
        error = validate_skill_name(name)
        if error:
            raise ValueError(error)
        current = self.get(name)
        meta: dict = {}
        description = (description or "").strip() or (
            current.description if current is not None else ""
        )
        if description:
            meta["description"] = description
        when_to_use = (
            when_to_use
            if when_to_use is not None
            else (current.when_to_use if current else "")
        )
        if str(when_to_use).strip():
            meta["when_to_use"] = str(when_to_use).strip()
        tags = tags if tags is not None else (current.tags if current else [])
        if tags:
            meta["tags"] = list(tags)
        priority = (
            priority if priority is not None else (current.priority if current else 0)
        )
        if priority:
            meta["priority"] = int(priority)
        embed = embed if embed is not None else (current.embed if current else None)
        if embed is not None:
            meta["embed"] = bool(embed)
        max_read_chars = (
            max_read_chars
            if max_read_chars is not None
            else (current.max_read_chars if current else DEFAULT_MAX_READ_CHARS)
        )
        if max_read_chars != DEFAULT_MAX_READ_CHARS:
            meta["max_read_chars"] = int(max_read_chars)

        self.user_dir.mkdir(parents=True, exist_ok=True)
        path = self.user_dir / f"{name}.md"
        text = (content or "").strip("\n")
        if meta:
            frontmatter = yaml.safe_dump(
                meta,
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
    """thinking.get_prompt 使用；技能目录为空时返回空串。"""
    try:
        return skill_manager.get_prompt_block()
    except Exception as e:
        log.warning(f"内嵌技能提示词生成失败:{e}")
        return ""
