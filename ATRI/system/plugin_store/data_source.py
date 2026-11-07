import asyncio
import contextlib
import hashlib
import importlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import anyio
import httpx
import nonebot
import yaml
from nonebot.plugin.load import path_to_module_name

from ATRI.dir import CONFIG_DIR, PLUGIN_CONFIG_DIR, PLUGIN_DATA_DIR
from ATRI.exceptions import PluginError
from ATRI.load import package_requirements, parse_requirement_line
from ATRI.log import log
from ATRI.service import ServiceTools
from ATRI.utils import git, request
from ATRI.utils.package_manager import PackageManager
from ATRI.version import compare_versions, parse_version

from . import progress
from .compat import CompatResult, PluginRequirement, normalize_type
from .compat import check as check_compat

PLUGINS_URL = "https://raw.githubusercontent.com/lokyoh/ATRI-LK-plugin/main/plugin.json"
FILE_URL = "https://api.github.com/repos/lokyoh/ATRI-LK-plugin/contents/{}?ref=main"
PLUGINS_DIR = Path(".") / "plugins"
DOWNLOAD_RETRIES = 3
DOWNLOAD_RETRY_DELAY = 1

_install_locks: dict[str, anyio.Lock] = {}
"""按插件名区分的互斥锁，避免并发写同一个插件目录。

用 ``anyio.Lock`` 而不是 ``asyncio.Lock``：后者一旦发生过竞争就会绑定到
当时的 event loop，换一个 loop 再获取会抛「bound to a different event loop」
（测试里每次 ``asyncio.run`` 都是一个新 loop，会直接踩到）。

不按目录加锁是按插件名：同名插件必然落在同一目录，不同插件互不阻塞。
"""


def _install_lock_for(plugin_name: str) -> anyio.Lock:
    lock = _install_locks.get(plugin_name)
    if lock is None:
        lock = anyio.Lock()
        _install_locks[plugin_name] = lock
    return lock


def _pip_install_args(req_path: Path) -> list[str]:
    """依赖安装的命令行参数，固定装进当前解释器。

    直接调 PATH 上的 ``pip`` 可能落到另一个解释器（venv / poetry / Docker
    里 PATH 未必指向正在运行的这个），装完当前进程仍然 import 不到。
    返回参数列表而非拼接后的字符串：调用方用 exec 直传，不经 shell。
    """
    return [sys.executable, "-m", "pip", "install", "-r", str(req_path)]


def _same_version(left: str, right: str) -> bool:
    """按数值逐段比较版本号，``1.2`` 与 ``1.2.0`` 视为同一版本。

    用字符串全等会让作者把 ``1.2`` 写成 ``1.2.0`` 时被误判成「需更新」，
    而更新后版本号依旧不等，于是永久提示更新。
    """
    left_parts = parse_version(left)
    right_parts = parse_version(right)
    if left_parts is None or right_parts is None:
        return str(left) == str(right)
    return compare_versions(left_parts, right_parts) == 0


async def _run_command(args: list[str], cwd: str | Path | None = None):
    """执行命令，参数以列表直传（不经 shell）。

    走 shell 会让插件目录名里的 ``&`` ``|`` ``;`` 被解释——这些名字来自
    外部仓库，属于不可控输入。
    """
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd) if cwd is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await process.communicate()
    stdout = stdout_bytes.decode(errors="replace")
    stderr = stderr_bytes.decode(errors="replace")
    if process.returncode != 0:
        raise subprocess.CalledProcessError(
            process.returncode,
            args,
            output=stdout,
            stderr=stderr,
        )
    return stdout, stderr


def _safe_rmtree(path: Path):
    """
    在 Windows 上更稳健地删除目录，处理只读文件和文件占用问题
    """
    import time

    if not path.exists():
        return

    def _remove_readonly(func, path, excinfo):
        """错误处理函数：移除只读属性后重试"""
        try:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            func(path)
        except Exception:
            pass

    # 最多重试 3 次
    for _ in range(3):
        try:
            shutil.rmtree(path, onerror=_remove_readonly)
            if not path.exists():
                return
        except Exception:
            pass
        time.sleep(0.2)
    # 如果仍然失败，尝试逐文件清理
    try:
        for item in list(path.iterdir()):
            try:
                if item.is_dir():
                    _safe_rmtree(item)
                else:
                    try:
                        os.chmod(item, stat.S_IWRITE | stat.S_IREAD)
                        item.unlink()
                    except Exception:
                        pass
            except Exception:
                pass
        path.rmdir()
    except Exception:
        pass


def _to_plugin_path(path: str | Path) -> Path:
    """
    把 plugin.json 里登记的插件路径统一成 ``Path``。

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


def uninstall_package(path: str | Path):
    path = _to_plugin_path(path)
    plugin_name = path.name
    requirements_file = path / "requirements.txt"
    if requirements_file.exists():
        try:
            log.debug(f"开始处理插件 {plugin_name} 的依赖")
            with open(requirements_file, encoding="utf-8") as f:
                lines = f.readlines()
            pm = PackageManager()
            uninstall_packages = []
            for line in lines:
                parsed = parse_requirement_line(line)
                if parsed:
                    package_name, _ = parsed
                    if package_name in package_requirements:
                        package_requirements[package_name].remove(plugin_name)
                        if not package_requirements[package_name]:
                            del package_requirements[package_name]
                            uninstall_packages.append(package_name)
                    else:
                        log.warning(f"插件 {package_name} 没有找到依赖关系")
            if uninstall_packages:
                log.debug(f"卸载 {plugin_name} 的依赖 {uninstall_packages}")
                pm.uninstall(uninstall_packages)
        except Exception as e:
            log.error(f"处理插件 {plugin_name} 的依赖时出错：{e}")


class PluginRuntimeManager:
    """插件运行时管理器，负责热重载已加载的插件模块。"""

    @staticmethod
    def _clear_nonebot_plugin_registry(module_name: str):
        """清理 NoneBot 中已经残留的插件注册信息，避免重复加载。"""
        nb_plugin = getattr(nonebot, "plugin", None)
        if nb_plugin is None:
            return
        plugin_module_name = module_name.rsplit(".", 1)[-1]
        stale_plugin_ids = []
        for plugin_id, plugin in list(getattr(nb_plugin, "_plugins", {}).items()):
            if (
                plugin.module_name == module_name
                or plugin.module_name.startswith(f"{module_name}.")
                or plugin.name == plugin_module_name
                or plugin.id_ == plugin_module_name
                or plugin.id_.startswith(f"{plugin_module_name}:")
            ):
                stale_plugin_ids.append(plugin_id)
        for plugin_id in stale_plugin_ids:
            plugin = nb_plugin._plugins.get(plugin_id)
            if plugin is not None:
                nb_plugin._revert_plugin(plugin)
        for manager in list(getattr(nb_plugin, "_managers", [])):
            removed_ids = []
            for plugin_id, controlled_module in list(
                manager.controlled_modules.items()
            ):
                if controlled_module == module_name or controlled_module.startswith(
                    f"{module_name}."
                ):
                    removed_ids.append(plugin_id)
            for plugin_id in removed_ids:
                if plugin_id in getattr(manager, "_third_party_plugin_ids", {}):
                    del manager._third_party_plugin_ids[plugin_id]
                if plugin_id in getattr(manager, "_searched_plugin_ids", {}):
                    del manager._searched_plugin_ids[plugin_id]
            if not manager.controlled_modules:
                with contextlib.suppress(ValueError):
                    nb_plugin._managers.remove(manager)

    @staticmethod
    def resolve_plugin_name(plugin_name: str) -> str:
        s = ServiceTools.get_service(plugin_name)
        if s is None:
            raise PluginError(f"没有发现插件: {plugin_name}")
        return s.module_name

    @staticmethod
    async def unload_plugin(plugin_name: str):
        module_name = PluginRuntimeManager.resolve_plugin_name(plugin_name)
        s = ServiceTools.get_service(plugin_name)
        if s:
            await s.unload()
        PluginRuntimeManager._clear_nonebot_plugin_registry(module_name)
        for key in list(sys.modules):
            if key == module_name or key.startswith(f"{module_name}."):
                del sys.modules[key]
        ServiceTools.service_list.pop(plugin_name, None)
        log.info(f"已卸载插件模块：{module_name}")

    @staticmethod
    async def reload_plugin(plugin_name: str):
        module_name = PluginRuntimeManager.resolve_plugin_name(plugin_name)
        await PluginRuntimeManager.unload_plugin(plugin_name)
        nonebot.load_plugin(module_name)
        module = importlib.import_module(module_name)
        log.success(f"已热重载插件：{module_name}")
        return module


def _repo_name(repo: str) -> str:
    """从仓库地址里取项目名，GitHub 型插件的本地目录名就是它。"""
    repo = str(repo).rstrip("/")
    if repo.endswith(".git"):
        repo = repo[:-4]
    return repo.split("/")[-1]


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
    def is_github(self) -> bool:
        """是否从 GitHub 仓库安装（含 index 里带 repo 与 version=github 两种写法）"""
        return bool(self.repo) or self.version == "github"

    @property
    def target_path(self) -> Path:
        """插件在 ``plugins/`` 下的落点。"""
        if self.repo:
            return PLUGINS_DIR / _repo_name(self.repo)
        return _to_plugin_path(self.path)

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


def _normalize_entry(name: str, data: Mapping) -> dict:
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


def _backup_plugin_dir(path: Path) -> Path | None:
    """把插件目录整体复制到临时位置，供更新失败时恢复。

    更新走的是「下载覆盖」而不是原地拉取，下载失败时清理逻辑会把目标
    目录删掉——若事先不备份，旧版本就一起没了。
    """
    if not path.exists():
        return None
    backup = Path(tempfile.mkdtemp(prefix="atri-plugin-backup-")) / path.name
    try:
        if path.is_dir():
            shutil.copytree(path, backup)
        else:
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
    except Exception as e:
        log.warning(f"备份 {path} 失败，更新将无法回滚：{e}")
        shutil.rmtree(backup.parent, ignore_errors=True)
        return None
    return backup


def _restore_plugin_dir(backup: Path | None, path: Path):
    """用备份恢复插件目录；备份不存在时什么都不做。"""
    if backup is None or not backup.exists():
        return
    try:
        if path.exists():
            if path.is_dir():
                _safe_rmtree(path)
            else:
                path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        if backup.is_dir():
            shutil.copytree(backup, path)
        else:
            shutil.copy2(backup, path)
        log.warning(f"已从备份恢复插件目录 {path}")
    except Exception as e:
        log.error(f"从备份恢复 {path} 失败：{e}")


def _drop_backup(backup: Path | None):
    if backup is None:
        return
    shutil.rmtree(backup.parent, ignore_errors=True)


def _remove_service_files(plugin_name: str):
    """删除插件残留的服务配置、插件设置与私有数据目录。

    这些文件都在 ``data/`` 下、不在插件目录里，只删插件目录会留下它们：
    重装同名插件会继承旧配置，卸载后也谈不上干净。
    """
    targets = [
        CONFIG_DIR / f"{plugin_name}.json",
        PLUGIN_CONFIG_DIR / f"{plugin_name}_config.json",
    ]
    for path in targets:
        try:
            if path.is_file():
                path.unlink()
                log.debug(f"已删除插件配置 {path}")
        except OSError as e:
            log.warning(f"删除插件配置 {path} 失败：{e}")
    data_dir = PLUGIN_DATA_DIR / plugin_name
    try:
        if data_dir.is_dir():
            _safe_rmtree(data_dir)
            log.debug(f"已删除插件数据目录 {data_dir}")
    except OSError as e:
        log.warning(f"删除插件数据目录 {data_dir} 失败：{e}")


def _validate_plugin_files(root: Path, is_dir: bool) -> list[str]:
    """安装前静态检查插件包，返回问题列表（空表示通过）。

    只做不需要执行插件代码的检查：文件在不在、能不能编译、``meta.yml``
    能不能解析。给 WebUI 的「强制安装」提供真实依据，而不是只弹个确认框。
    """
    problems: list[str] = []
    if not root.exists():
        return [f"插件文件不存在：{root}"]

    if not is_dir:
        entry_file = root if root.suffix == ".py" else root.with_name(f"{root.name}.py")
        if not entry_file.is_file():
            return [f"插件入口文件不存在：{entry_file}"]
    else:
        entry_file = root / "__init__.py"
        if not entry_file.is_file():
            problems.append(f"目录内缺少 __init__.py：{root}")

    for py_file in [entry_file] if entry_file.is_file() else []:
        try:
            compile(py_file.read_text(encoding="utf-8"), str(py_file), "exec")
        except SyntaxError as e:
            problems.append(f"{py_file.name} 语法错误：第 {e.lineno} 行 {e.msg}")
        except OSError as e:
            problems.append(f"{py_file.name} 读取失败：{e}")

    meta_path = (root if is_dir else root.parent) / "meta.yml"
    if is_dir and meta_path.is_file():
        try:
            meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            problems.append(f"meta.yml 解析失败：{e}")
        else:
            if not isinstance(meta, dict):
                problems.append("meta.yml 顶层应为映射")
            elif not str(meta.get("version") or "").strip():
                problems.append("meta.yml 缺少 version")

    return problems


class PluginManager:
    plugin_list: ClassVar[dict] = {}
    _fetch_lock: ClassVar[anyio.Lock | None] = None
    """索引拉取锁，把并发请求合并成一次，避免同时打多次 GitHub。"""

    @classmethod
    def _lock(cls) -> anyio.Lock:
        if cls._fetch_lock is None:
            cls._fetch_lock = anyio.Lock()
        return cls._fetch_lock

    @classmethod
    async def fresh_list(cls):
        log.debug("更新插件列表信息")
        data = await request.get(PLUGINS_URL)
        raw = data.json()
        if not isinstance(raw, dict):
            raise PluginError("插件索引格式错误：顶层应是插件名到信息的映射")
        cls.plugin_list = {
            name: _normalize_entry(name, info if isinstance(info, dict) else {})
            for name, info in raw.items()
        }

    @classmethod
    async def ensure_list(cls) -> dict:
        """确保列表可用；已有数据时不联网。

        索引只在手动刷新时才更新，日常读取走内存，避免每翻一页、每开一次
        WebUI 都去拉一次 GitHub。
        """
        if cls.plugin_list:
            return cls.plugin_list
        async with cls._lock():
            if cls.plugin_list:
                return cls.plugin_list
            try:
                await cls.fresh_list()
            except Exception as e:
                log.warning(f"更新插件列表失败：{e}")
        return cls.plugin_list

    @classmethod
    async def refresh_list(cls) -> dict:
        """强制联网刷新，供手动触发的场景使用。"""
        async with cls._lock():
            await cls.fresh_list()
        return cls.plugin_list

    @classmethod
    async def get_plugin_list(cls) -> dict:
        """兼容旧调用方：语义等同于 ``ensure_list``。"""
        return await cls.ensure_list()

    @classmethod
    async def check_list(cls):
        if not cls.plugin_list:
            await cls.ensure_list()
        if not cls.plugin_list:
            raise PluginError("更新列表失败")

    @classmethod
    @staticmethod
    def read_local_meta(plugin_name: str, raw: Mapping) -> dict:
        """读已装插件目录下的 ``meta.yml``，纯本地、不联网。

        列表接口为了性能不拉远端 meta.yml，导致只在 meta.yml 里声明
        ``lkapi`` 的仓库型插件在列表页看不到提示。已装的插件本地就有这份
        文件，直接读即可；未装或读取失败时返回空字典。
        """
        try:
            entry = PluginEntry.from_mapping(plugin_name, raw)
            root = entry.target_path
        except Exception:
            return {}
        meta_path = root / "meta.yml"
        if not meta_path.is_file():
            return {}
        try:
            meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as e:
            log.debug(f"读取本地 meta.yml 失败（{meta_path}）：{e}")
            return {}
        return meta if isinstance(meta, dict) else {}

    @classmethod
    async def get_entry(cls, plugin_name: str) -> PluginEntry:
        """取一条插件记录，仓库型插件会用远端 ``meta.yml`` 补全信息。"""
        if plugin_name not in cls.plugin_list:
            raise PluginError(f"找不到插件 {plugin_name}")
        raw = cls.plugin_list[plugin_name]
        entry = PluginEntry.from_mapping(plugin_name, raw)
        if not entry.repo:
            return entry
        try:
            meta = await cls.get_github_plugin_meta(entry.repo)
        except Exception as e:
            log.warning(f"获取插件`{plugin_name}`的 meta.yml 失败：{e}")
            return entry
        if not isinstance(meta, dict):
            return entry
        merged = {**raw, **{k: v for k, v in meta.items() if v is not None}}
        merged["repo"] = entry.repo
        return PluginEntry.from_mapping(
            plugin_name,
            merged,
            fallback_author=entry.author,
        )

    @classmethod
    async def get_entries(cls, names: list[str] | None = None) -> list[PluginEntry]:
        """批量取插件记录，单条 meta.yml 失败不影响其它条目。"""
        if names is None:
            names = list(cls.plugin_list)
        entries = []
        for name in names:
            if name not in cls.plugin_list:
                continue
            try:
                entries.append(await cls.get_entry(name))
            except PluginError:
                continue
        return entries

    @classmethod
    def compat(cls, entry: PluginEntry) -> CompatResult:
        """插件要求与当前环境的比对结果（仅用于标注，不拦截安装）。"""
        return check_compat(entry.requirement)

    @classmethod
    async def install_plugin(cls, plugin_name: str, load: bool = False):
        if plugin_name not in cls.plugin_list:
            log.info(f"安装未知插件`{plugin_name}`")
            raise PluginError(f"找不到插件 {plugin_name}")
        entry = PluginEntry.from_mapping(plugin_name, cls.plugin_list[plugin_name])
        compat = cls.compat(entry)
        if not compat.ok:
            log.warning(f"插件`{plugin_name}`与当前环境不匹配：{compat.reason}")
        progress.start(plugin_name, "install")
        try:
            async with _install_lock_for(plugin_name):
                await cls._install_plugin(plugin_name, entry, load)
        except Exception as e:
            progress.finish(plugin_name, ok=False, message=str(e))
            raise
        progress.finish(plugin_name, ok=True, message="安装完成")

    @classmethod
    async def _install_plugin(cls, plugin_name: str, entry: PluginEntry, load: bool):
        if entry.repo:
            path = await cls.install_github_plugin(entry.repo)
            if load:
                if not path or not str(path).strip():
                    raise PluginError(
                        f"安装插件 {plugin_name} 失败：未返回有效插件路径 {path}"
                    )
                nonebot.load_plugin(path_to_module_name(Path(path)))
            return
        res_list = entry.res
        try:
            if res_list:
                log.info(f"开始为插件`{plugin_name}`下载资源")
                progress.update(plugin_name, "resource", f"共 {len(res_list)} 个资源")
                for res in res_list:
                    data = await request.get(FILE_URL.format(res))
                    if data.status_code != 200:
                        log.warning(
                            f"在下载插件`{plugin_name}`的资源时网络连接错误，code:{data.status_code}"
                        )
                        raise PluginError(f"网络连接错误，code:{data.status_code}")
                    if "." in res:
                        file = data.json()
                        file_list = [file]
                    else:
                        file_list = data.json()
                    await cls.download_github_file(file_list)
        except Exception as e:
            log.warning(f"插件`{plugin_name}`资源安装失败:发生错误{e}")
            raise PluginError(f"插件资源安装失败:{e}")
        path: str = entry.path
        p_path = path.replace(".", "/")
        try:
            try:
                log.debug(f"开始下载插件`{plugin_name}`")
                progress.update(plugin_name, "fetch", path)
                if entry.is_dir:
                    data = await request.get(FILE_URL.format(p_path))
                    if data.status_code != 200:
                        raise PluginError(f"网络连接错误,code:{data.status_code}")
                    file_list = data.json()
                else:
                    p_path += ".py"
                    data = await request.get(FILE_URL.format(p_path))
                    if data.status_code != 200:
                        raise PluginError(f"网络连接错误,code:{data.status_code}")
                    file = data.json()
                    file_list = [file]
                await cls.download_github_file(file_list)
            except PluginError:
                # 清理已下载的文件或目录
                try:
                    target_path = Path(".") / p_path
                    if target_path.exists():
                        log.info(f"清理插件目录 {target_path}...")
                        if entry.is_dir:
                            _safe_rmtree(target_path)
                        else:
                            target_path.unlink()
                        log.info(f"已清理插件 {plugin_name} 的残留文件")
                except Exception as cleanup_error:
                    log.error(f"清理插件 {plugin_name} 残留文件失败: {cleanup_error}")
                raise
            target = Path(".") / p_path
            progress.update(plugin_name, "validate")
            problems = _validate_plugin_files(target, entry.is_dir)
            if problems:
                for problem in problems:
                    log.warning(f"插件`{plugin_name}`校验未通过：{problem}")
                raise PluginError(f"插件校验未通过：{'；'.join(problems)}")
            req_path = Path(".") / p_path / "requirements.txt"
            if req_path.exists():
                log.info(f"开始为`{plugin_name}`安装依赖")
                progress.update(plugin_name, "deps")
                stdout, _ = await _run_command(_pip_install_args(req_path))
                log.debug(f"`{plugin_name}`依赖安装信息: stdout={stdout}")
            if load:
                progress.update(plugin_name, "load")
                nonebot.load_plugin(path)
            log.info(f"插件`{plugin_name}`安装结束")
        except Exception as e:
            log.warning(f"插件`{plugin_name}`安装失败:发生错误{e}")
            raise PluginError(f"插件安装失败:{e}")

    @classmethod
    async def remove_plugin(cls, plugin_name: str):
        if plugin_name not in ServiceTools.service_list:
            raise PluginError(f"未安装插件 {plugin_name}")
        if plugin_name not in cls.plugin_list:
            raise PluginError(f"无法卸载 {plugin_name}，请手动卸载")
        entry = PluginEntry.from_mapping(plugin_name, cls.plugin_list[plugin_name])
        progress.start(plugin_name, "remove", "cleanup")
        try:
            async with _install_lock_for(plugin_name):
                if entry.repo:
                    await cls.remove_github_plugin(entry.repo)
                else:
                    raw_path = entry.path
                    if not raw_path:
                        raise PluginError(
                            f"插件 {plugin_name} 未登记路径信息，请手动卸载"
                        )
                    path = _to_plugin_path(raw_path)
                    if not entry.is_dir:
                        if path.suffix != ".py":
                            path = path.with_name(f"{path.name}.py")
                        if path.exists():
                            os.remove(path)
                        else:
                            log.warning(f"插件文件 {path} 不存在，跳过删除")
                    else:
                        uninstall_package(path)
                        _safe_rmtree(path)
                        if path.exists():
                            log.warning(f"插件目录 {path} 未能完全删除，请手动清理")
                await PluginRuntimeManager.unload_plugin(plugin_name)
                _remove_service_files(plugin_name)
        except PluginError as e:
            progress.finish(plugin_name, ok=False, message=e.prompt)
            raise
        except Exception as e:
            progress.finish(plugin_name, ok=False, message=str(e))
            raise
        progress.finish(plugin_name, ok=True, message="移除完成")
        log.info(f"插件`{plugin_name}`移除成功")

    @classmethod
    async def install_github_plugin(cls, repo: str) -> str:
        log.info(f"开始从 GitHub 克隆插件：{repo}")
        repo_name = _repo_name(repo)
        target_path = PLUGINS_DIR / repo_name
        try:
            # 如果目录已存在，先删除
            if target_path.exists():
                log.warning(f"目录 {target_path} 已存在，正在删除...")
                _safe_rmtree(target_path)
            cloned = await git.clone(repo, target_path)
            if not cloned.ok:
                raise PluginError(f"Git clone 失败：{cloned.detail}")
            log.debug(f"Git clone 输出：{cloned.stdout}")
            log.info(f"成功克隆插件：{repo_name} 到 {target_path}")
            # 检查并安装依赖
            req_path = target_path / "requirements.txt"
            if req_path.exists():
                log.info(f"开始为 `{repo_name}` 安装依赖")
                stdout, _ = await _run_command(_pip_install_args(req_path))
                log.debug(f"`{repo_name}` 依赖安装信息：{stdout}")
                log.info("依赖安装完成")
            log.info(f"GitHub 插件 `{repo_name}` 安装结束")
            return str(target_path)
        except PluginError as e:
            log.error(f"Git clone 失败：{e.prompt}")
            if target_path.exists():
                log.info(f"清理插件目录 {target_path}...")
                _safe_rmtree(target_path)
            raise
        except Exception as e:
            log.error(f"安装 GitHub 插件失败：{e}")
            if target_path.exists():
                log.info(f"清理插件目录 {target_path}...")
                _safe_rmtree(target_path)
            raise PluginError(f"安装 GitHub 插件失败：{e}")

    @classmethod
    async def remove_github_plugin(cls, repo: str):
        log.info(f"开始卸载 GitHub插件：{repo}")
        try:
            repo_name = _repo_name(repo)
            target_path = PLUGINS_DIR / repo_name
            # 检查目录是否存在
            if not target_path.exists():
                raise PluginError(f"插件目录 {target_path} 不存在")
            uninstall_package(target_path)
            # 删除插件目录（使用安全删除）
            _safe_rmtree(target_path)
            log.info(f"成功卸载 GitHub插件：{repo_name}")
        except Exception as e:
            log.error(f"卸载 GitHub插件失败：{e}")
            raise PluginError(f"卸载 GitHub插件失败：{e}")

    @classmethod
    async def update_github_plugin(cls, repo: str):
        log.info(f"开始更新 GitHub 插件：{repo}")
        repo_name = _repo_name(repo)
        target_path = PLUGINS_DIR / repo_name
        previous_commit = ""
        try:
            if not target_path.exists():
                raise PluginError(f"插件目录 {target_path} 不存在，请先安装插件")
            if not (target_path / ".git").exists():
                raise PluginError(f"{target_path} 不是 git 仓库，无法更新")
            previous_commit = await git.head_commit(target_path)
            pulled = await git.pull(target_path)
            if not pulled.ok:
                raise PluginError(f"Git pull 失败：{pulled.detail}")
            log.debug(f"Git pull 输出：{pulled.stdout}")
            stdout = pulled.stdout
            if "Already up to date" in stdout or "已经是最新的" in stdout:
                log.info(f"GitHub 插件 `{repo_name}` 已经是最新版本")
            else:
                log.info(f"成功更新 GitHub 插件：{repo_name}")
                req_path = target_path / "requirements.txt"
                if req_path.exists():
                    log.info(f"开始为 `{repo_name}` 安装依赖")
                    stdout, _ = await _run_command(_pip_install_args(req_path))
                    log.debug(f"`{repo_name}` 依赖安装信息：{stdout}")
                    log.info("依赖安装完成")
            log.info(f"GitHub 插件 `{repo_name}` 更新结束")
        except Exception as e:
            detail = getattr(e, "stderr", "") or str(e)
            log.error(f"更新 GitHub 插件失败：{detail}")
            await cls._rollback_github_plugin(target_path, previous_commit)
            raise PluginError(f"更新 GitHub 插件失败：{detail}") from e

    @staticmethod
    async def _git_head(repo_path: Path) -> str:
        """取当前 commit；取不到返回空串（不影响后续流程）。"""
        return await git.head_commit(repo_path)

    @classmethod
    async def _rollback_github_plugin(cls, target_path: Path, commit: str):
        """把插件目录退回到更新前的 commit，并撤销 pull 的改动。

        回滚同样可能失败（例如工作区被外部改动），失败只记日志：此时抛出
        的仍是「更新失败」，不该被回滚错误盖掉。
        """
        await git.rollback_to(commit, cwd=target_path)

    @classmethod
    async def check_github_plugin_update(cls, repo: str):
        """
        检查 GitHub 插件是否有更新
        :param repo: GitHub 仓库地址
        :return: dict 包含更新信息 {'has_update': bool, 'local_version': str, 'remote_version': str}
        """
        log.debug(f"开始检查 GitHub 插件更新：{repo}")
        try:
            repo_name = _repo_name(repo)
            target_path = PLUGINS_DIR / repo_name
            # 检查目录是否存在
            if not target_path.exists():
                raise PluginError(f"插件目录 {target_path} 不存在")
            # 读取本地 meta.yml
            local_meta_path = target_path / "meta.yml"
            if not local_meta_path.exists():
                raise PluginError("未找到本地 meta.yml 文件")
            async with await anyio.open_file(
                local_meta_path, "r", encoding="utf-8"
            ) as f:
                local_meta = yaml.safe_load(await f.read()) or {}
            local_version = local_meta.get("version", "unknown")
            # 获取远程 meta.yml
            remote_meta = await cls.get_github_plugin_meta(repo)
            remote_version = (remote_meta or {}).get("version", "unknown")
            # 比较版本号
            has_update = local_version != remote_version
            result = {
                "has_update": has_update,
                "local_version": local_version,
                "remote_version": remote_version,
                "repo_name": repo_name,
            }
            if has_update:
                log.debug(
                    f"发现新版本：{repo_name} {local_version} -> {remote_version}"
                )
            return result
        except yaml.YAMLError as e:
            log.error(f"解析 meta.yml 失败：{e}")
            raise PluginError(f"解析 meta.yml 失败：{e}")
        except Exception as e:
            log.error(f"检查 GitHub 插件更新失败：{e}")
            raise PluginError(f"检查 GitHub 插件更新失败：{e}")

    @staticmethod
    async def get_github_plugin_meta(repo: str):
        """
        获取 GitHub 插件的 meta.yml 信息
        :param repo: GitHub 仓库地址
        :return: dict 包含 meta 信息
        """
        if repo.startswith("https://github.com/"):
            # 从 https://github.com/user/repo 转换为 https://raw.githubusercontent.com/user/repo/main/meta.yml
            repo_path = repo.replace("https://github.com/", "").rstrip("/")
            remote_meta_url = (
                f"https://raw.githubusercontent.com/{repo_path}/main/meta.yml"
            )
        else:
            raise PluginError(f"不支持的仓库类型：{repo}")
        response = await request.get(remote_meta_url, follow_redirects=True)
        if response.status_code != 200:
            raise PluginError(f"无法获取远程 meta.yml，状态码：{response.status_code}")
        return yaml.safe_load(response.text)

    @staticmethod
    async def download_github_file(file_list):
        """
        下载 GitHub 文件
        :param file_list: 下载保存路径列表
        :return: None
        """
        for file in file_list:
            file_path = Path(".") / file["path"]
            temporary_path = file_path.with_name(f".{file_path.name}.part")
            file_path.parent.mkdir(parents=True, exist_ok=True)
            last_error = None
            for attempt in range(1, DOWNLOAD_RETRIES + 1):
                try:
                    data = await request.get(file["download_url"])
                    if data.status_code != 200:
                        raise PluginError(
                            f"下载文件失败: {file_path}, 状态码: {data.status_code}"
                        )
                    content = data.content
                    if not content:
                        raise PluginError(f"下载的文件为空: {file_path}")
                    expected_sha = file.get("sha")
                    if expected_sha:
                        blob = f"blob {len(content)}\0".encode() + content
                        actual_sha = hashlib.sha1(blob).hexdigest()
                        if actual_sha != expected_sha:
                            raise PluginError(
                                f"下载文件校验失败: {file_path}, "
                                f"期望 {expected_sha}, 实际 {actual_sha}"
                            )
                    log.debug(f"下载文件`{file_path}`")
                    async with await anyio.open_file(temporary_path, "wb") as f:
                        await f.write(content)
                    os.replace(temporary_path, file_path)
                    break
                except (httpx.HTTPError, OSError, PluginError) as e:
                    last_error = e
                    temporary_path.unlink(missing_ok=True)
                    if attempt < DOWNLOAD_RETRIES:
                        log.warning(
                            f"下载文件`{file_path}`失败，第 {attempt} 次重试: {e}"
                        )
                        await asyncio.sleep(DOWNLOAD_RETRY_DELAY)
                    else:
                        raise PluginError(
                            f"下载文件失败（重试 {DOWNLOAD_RETRIES} 次）: "
                            f"{file_path}: {last_error}"
                        ) from e

    @classmethod
    async def check_update(cls, plugin_name: str):
        if plugin_name not in cls.plugin_list:
            return None
        entry = PluginEntry.from_mapping(plugin_name, cls.plugin_list[plugin_name])
        version = entry.version
        if plugin_name in ServiceTools.service_list:
            if version == "github":
                try:
                    r = await cls.check_github_plugin_update(entry.repo)
                except PluginError as e:
                    log.warning(f"插件`{plugin_name}`检查更新失败：{e.prompt}")
                    return None
                if r["has_update"]:
                    return r["local_version"], r["remote_version"]
                return None
            now_version = ServiceTools(plugin_name).load_service().version
            if not _same_version(now_version, version):
                return now_version, version
        return None

    @classmethod
    async def update_plugin(cls, plugin_name: str, force: bool = False):
        """更新一个插件。

        ``force=True`` 时跳过「是否已有新版」的判断直接重装：用于版本号没变
        但内容需要修复、或上次更新不完整的情况。WebUI 的「强制更新」走这里。
        """
        if plugin_name not in cls.plugin_list:
            return False, f"找不到插件 {plugin_name}"
        entry = PluginEntry.from_mapping(plugin_name, cls.plugin_list[plugin_name])
        version = entry.version
        compat = cls.compat(entry)
        if not compat.ok:
            log.warning(f"插件`{plugin_name}`与当前环境不匹配：{compat.reason}")

        if (
            not force
            and plugin_name in ServiceTools.service_list
            and await cls.check_update(plugin_name) is None
        ):
            return False, f"{plugin_name} 已是最新版本，无需更新（可用强制更新重装）"
        progress.start(plugin_name, "update")
        backup: Path | None = None
        target: Path | None = None
        if not entry.repo:
            try:
                target = entry.target_path
            except PluginError:
                target = None
            if target is not None:
                backup = _backup_plugin_dir(target)
        try:
            async with _install_lock_for(plugin_name):
                if entry.repo:
                    progress.update(plugin_name, "fetch")
                    await cls.update_github_plugin(entry.repo)
                else:
                    await cls._install_plugin(plugin_name, entry, False)
        except PluginError as e:
            _restore_plugin_dir(backup, target)
            progress.finish(plugin_name, ok=False, message=e.prompt)
            return False, f"更新插件失败：{e.prompt}"
        except Exception:
            _restore_plugin_dir(backup, target)
            raise
        else:
            _drop_backup(backup)

        hint = f"（注意：{compat.reason}）" if not compat.ok else ""
        try:
            progress.update(plugin_name, "reload")
            await PluginRuntimeManager.reload_plugin(plugin_name)
            progress.finish(plugin_name, ok=True, message="更新完成，已热重载")
            return True, f"{plugin_name}-{version} 更新成功，已热重载插件{hint}"
        except Exception as e:
            log.warning(f"插件 {plugin_name} 热重载失败：{e}")
            progress.finish(plugin_name, ok=True, message="更新完成，需重启生效")
            return (
                True,
                f"{plugin_name}-{version}安装成功，请重启以启用新版插件{hint}",
            )
