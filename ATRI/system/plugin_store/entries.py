from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ATRI.exceptions import PluginError

from . import state
from .compat import PluginRequirement, normalize_type


def repo_name(repo: str) -> str:
    """从仓库地址里取项目名，GitHub 型插件的本地目录名就是它。"""
    repo = str(repo).rstrip("/")
    if repo.endswith(".git"):
        repo = repo[:-4]
    return repo.split("/")[-1]


def to_plugin_path(path: str | Path) -> Path:
    """把 plugin.json 里登记的插件路径统一成 ``Path``。

    登记值通常是点分模块路径（``plugins.anti_effort``），也兼容文件系统路径
    （``plugins/anti_effort``、``plugins/anti_effort.py``）。已经是 ``Path``
    的入参原样返回，所有下游都可以安全地访问 ``.name`` / ``.suffix`` 等属性。
    """
    if isinstance(path, Path):
        return path
    raw = str(path).strip().replace("\\", "/")
    if not raw:
        raise PluginError("插件路径为空，无法定位插件目录")
    if "/" in raw:
        return Path(raw)
    parts = raw.split(".")
    # 点分写法末尾的 ``py`` 是文件后缀而非目录层级：plugins.coser.py
    if len(parts) > 1 and parts[-1].lower() == "py":
        resolved = Path(*parts[:-1])
        return resolved.with_name(f"{resolved.name}.py")
    return Path(*parts)


@dataclass
class PluginEntry:
    """商店里的一条插件记录。

    索引（``plugin.json``）与插件自带 ``meta.yml`` 的字段都归一到此处，
    下游不再各自处理缺失键。``version`` 为 ``github`` 表示仓库型插件，
    真实版本由 ``meta.yml`` 提供。
    """

    name: str
    """插件名，即 ``plugin.json`` 的键"""
    entry: dict
    """该条目的原始数据，保留未识别字段供调用方使用"""
    path: str = ""
    """非仓库型插件的点分模块路径"""
    repo: str = ""
    """仓库型插件的仓库地址"""
    docs: str = ""
    version: str = ""
    author: str = ""
    is_dir: bool = True
    res: list = field(default_factory=list)
    requirement: PluginRequirement = field(default_factory=PluginRequirement)

    @property
    def target_path(self) -> Path:
        """插件在磁盘上的实际落点。"""
        if self.repo:
            return state.PLUGINS_DIR / repo_name(self.repo)
        path = to_plugin_path(self.path)
        if not self.is_dir and path.suffix != ".py":
            # 单文件型落点是 ``plugins/<name>.py``，不带 .py 会指向不存在的路径，
            # 备份/清理就会作用在错误的位置
            return path.with_name(f"{path.name}.py")
        return path

    @classmethod
    def from_mapping(
        cls,
        name: str,
        data: Mapping | None,
        *,
        fallback_type: str = "",
        fallback_author: str = "",
    ) -> "PluginEntry":
        """由 ``plugin.json`` 条目或 ``meta.yml`` 映射构造记录。"""
        data = data or {}
        version = str(data.get("version") or "")
        repo = str(data.get("repo") or "").strip()
        requirement = PluginRequirement.from_mapping(data)
        if fallback_type and not requirement.type:
            requirement = PluginRequirement(
                type=fallback_type,
                framework=requirement.framework,
                lkapi=requirement.lkapi,
            )
        return cls(
            name=name,
            entry=dict(data),
            path=str(data.get("path") or ""),
            repo=repo,
            docs=str(data.get("docs") or ""),
            version=version,
            author=str(data.get("author") or fallback_author or ""),
            is_dir=bool(data.get("is_dir", True)),
            res=list(data.get("res") or []),
            requirement=requirement,
        )


def normalize_entry(name: str, data: Mapping) -> dict:
    """把单条索引记录补齐成稳定的形状（保留原有键）。"""
    entry = dict(data)
    entry["docs"] = str(entry.get("docs") or "")
    entry["version"] = str(entry.get("version") or "")
    entry["author"] = str(entry.get("author") or "")
    entry["is_dir"] = bool(entry.get("is_dir", True))
    entry["type"] = normalize_type(entry.get("type") or entry.get("plugin_type"))
    requirement = PluginRequirement.from_mapping(entry)
    entry["framework"] = requirement.framework
    entry["lkapi"] = requirement.lkapi
    return entry
