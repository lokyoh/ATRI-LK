import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ATRI.exceptions import PluginError

from . import state
from .compat import PluginRequirement, normalize_type

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
"""插件目录名白名单。GitHub 仓库名只允许这些字符，``..``/``C:`` 一律挡掉。"""


def _safe_name(name: str, where: str) -> str:
    """校验单层目录名，不合规直接抛错。

    这些值最终会进 ``safe_rmtree`` / ``copytree``：``repo=".../.."`` 的落点是
    项目根，一次「移除插件」就能把整个仓库删掉。
    """
    if not name or not _SAFE_NAME_RE.match(name) or ".." in name:
        raise PluginError(f"{where} 不是合法的插件目录名：{name!r}")
    return name


def repo_name(repo: str) -> str:
    """从仓库地址里取项目名，GitHub 型插件的本地目录名就是它。"""
    repo = str(repo).rstrip("/")
    if repo.endswith(".git"):
        repo = repo[:-4]
    return _safe_name(repo.split("/")[-1], f"仓库 {repo!r}")


def _no_parent_refs(path: Path) -> Path:
    """路径里不许出现 ``..``。

    绝对路径照收（插件可能装在项目外），但 ``plugins/../../x`` 这种相对逃逸
    必须挡掉：它最终会进 ``safe_rmtree``。
    """
    if ".." in path.parts:
        raise PluginError(f"插件路径越界：{path}")
    return path


def to_plugin_path(path: str | Path) -> Path:
    """把 plugin.json 里登记的插件路径统一成 ``Path``。

    登记值通常是点分模块路径（``plugins.anti_effort``），也兼容文件系统路径
    （``plugins/anti_effort``、``plugins/anti_effort.py``）。已经是 ``Path``
    的入参原样返回，所有下游都可以安全地访问 ``.name`` / ``.suffix`` 等属性。
    """
    if isinstance(path, Path):
        return _no_parent_refs(path)
    raw = str(path).strip().replace("\\", "/")
    if not raw:
        raise PluginError("插件路径为空，无法定位插件目录")
    if "/" in raw:
        return _no_parent_refs(Path(raw))
    parts = raw.split(".")
    # 点分写法末尾的 ``py`` 是文件后缀而非目录层级：plugins.coser.py
    if len(parts) > 1 and parts[-1].lower() == "py":
        resolved = Path(*parts[:-1])
        return _no_parent_refs(resolved.with_name(f"{resolved.name}.py"))
    return _no_parent_refs(Path(*parts))


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
            return _no_parent_refs(state.PLUGINS_DIR / repo_name(self.repo))
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
        res = data.get("res") or []
        # 索引里把 res 写成字符串时，list() 会拆成单个字符
        res_list = [str(item) for item in res] if isinstance(res, list | tuple) else []
        return cls(
            name=name,
            entry=dict(data),
            path=str(data.get("path") or ""),
            repo=repo,
            docs=str(data.get("docs") or ""),
            version=version,
            author=str(data.get("author") or fallback_author or ""),
            is_dir=bool(data.get("is_dir", True)),
            res=res_list,
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
