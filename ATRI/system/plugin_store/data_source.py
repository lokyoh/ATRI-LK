import asyncio
import contextlib
import hashlib
import importlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import anyio
import httpx
import nonebot
import yaml
from nonebot.plugin.load import path_to_module_name

from ATRI.dir import CONFIG_DIR, PLUGIN_CONFIG_DIR, PLUGIN_DATA_DIR, RES_DIR
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
#: git trees API：一次请求拿到仓库全部层级，contents API 只列一层
TREES_URL = (
    "https://api.github.com/repos/lokyoh/ATRI-LK-plugin/git/trees/main?recursive=1"
)
#: 文件内容走 raw，不计入 API 限额
RAW_URL = "https://raw.githubusercontent.com/lokyoh/ATRI-LK-plugin/main/{}"
PLUGINS_DIR = Path(".") / "plugins"
DOWNLOAD_RETRIES = 3
DOWNLOAD_RETRY_DELAY = 1

#: 仓库文件清单的缓存秒数。装多个插件时没必要每个都重新列一次仓库。
TREE_CACHE_TTL = 600
_tree_cache: dict[str, tuple[float, dict[str, str]]] = {}

#: 远端 meta.yml 的缓存秒数。适配版本只认远端，但列表要逐条读，
#: 不缓存等于每翻一页把所有仓库再打一遍。
META_CACHE_TTL = 600
_meta_cache: dict[str, tuple[float, dict]] = {}
"""仓库地址 → (取到的时间戳, meta)。只缓存成功结果，失败下次重试。"""

#: 单个 meta.yml 的超时。远端挂住时不能拖住整个列表请求。
META_TIMEOUT = 8.0

#: 更新检查结果的缓存秒数。列表只读这份缓存，不再逐条联网。
UPDATE_CACHE_TTL = 600
_update_cache: dict[str, tuple[float, tuple[str, str] | None]] = {}
"""插件名 → (检查时间, (本地版本, 远端版本) 或 None)。

列表页只读它，没有就是「未知」；真正的联网检查在「检查更新」入口做。
"""

#: 批量检查更新的总预算：超时的条目按未知返回，不拖住整个请求。
UPDATE_CHECK_BUDGET = 10.0

#: 索引拉取超时。索引只有几 KB，远端挂住时没必要等满全局的 30s。
INDEX_TIMEOUT = 8.0

#: 远端 meta 拉取失败后的冷却秒数。失败本身不进正缓存（断网恢复要能立刻拉到），
#: 但要挡住「每次打开页面都把 N 个仓库重试一遍」。
META_FAILURE_COOLDOWN = 60.0
_meta_failures: dict[str, float] = {}

#: 索引落盘位置。断网启动时商店仍有内容，不用整页空白。
INDEX_CACHE_FILE = Path(".") / "data" / "plugin_store" / "index.json"
INDEX_CACHE_VERSION = 1


def _meta_cached(repo: str) -> dict | None:
    item = _meta_cache.get(repo)
    if item is None:
        return None
    stamped, meta = item
    if time.time() - stamped > META_CACHE_TTL:
        _meta_cache.pop(repo, None)
        return None
    return meta


def _meta_in_cooldown(repo: str) -> bool:
    stamped = _meta_failures.get(repo)
    if stamped is None:
        return False
    if time.time() - stamped > META_FAILURE_COOLDOWN:
        _meta_failures.pop(repo, None)
        return False
    return True


def _remember_update_state(plugin_name: str, state: tuple[str, str] | None):
    _update_cache[plugin_name] = (time.time(), state)


def _forget_update_state(plugin_name: str):
    """装/卸/更新之后，旧的检查结果就不成立了。"""
    _update_cache.pop(plugin_name, None)


def cached_update_state(plugin_name: str) -> tuple[str, str] | None:
    """上次检查到的更新；从未检查过或已过期都返回 None。"""
    item = _update_cache.get(plugin_name)
    if item is None:
        return None
    stamped, state = item
    if time.time() - stamped > UPDATE_CACHE_TTL:
        _update_cache.pop(plugin_name, None)
        return None
    return state


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


def _save_index_cache(plugin_list: dict):
    """把索引写到本地。写失败只记日志：缓存是锦上添花，不该影响刷新本身。"""
    payload = {
        "version": INDEX_CACHE_VERSION,
        "saved_at": time.time(),
        "plugins": plugin_list,
    }
    try:
        INDEX_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = INDEX_CACHE_FILE.with_suffix(".json.part")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, INDEX_CACHE_FILE)
    except OSError as e:
        log.warning(f"写入插件索引缓存失败：{e}")


def _load_index_cache() -> dict:
    """读上次落盘的索引；不存在、损坏或版本不符都返回空字典。"""
    if not INDEX_CACHE_FILE.is_file():
        return {}
    try:
        payload = json.loads(INDEX_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning(f"读取插件索引缓存失败：{e}")
        return {}
    if not isinstance(payload, dict):
        return {}
    if payload.get("version") != INDEX_CACHE_VERSION:
        return {}
    plugins = payload.get("plugins")
    if not isinstance(plugins, dict):
        return {}
    return {
        name: _normalize_entry(name, info if isinstance(info, dict) else {})
        for name, info in plugins.items()
    }


_install_gate: anyio.Lock | None = None


def _global_install_gate() -> anyio.Lock:
    """全局安装互斥：装依赖与改 ``sys.modules`` 不能并行。"""
    global _install_gate
    if _install_gate is None:
        _install_gate = anyio.Lock()
    return _install_gate


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


def _register_requirements(path: str | Path):
    """把插件 requirements.txt 里的包登记到依赖账本。

    ``load.py`` 只在启动扫描 ``plugins/`` 时记账，运行时装出来的插件不在账上，
    卸载时 ``uninstall_package`` 找不到依赖关系就不会卸包，pip 包永久留下。
    """
    path = _to_plugin_path(path)
    requirements_file = path / "requirements.txt"
    if not requirements_file.is_file():
        return
    try:
        lines = requirements_file.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        log.warning(f"读取 {requirements_file} 失败，依赖未登记：{e}")
        return
    for line in lines:
        parsed = parse_requirement_line(line)
        if not parsed:
            continue
        package_name, _ = parsed
        owners = package_requirements.setdefault(package_name, [])
        if path.name not in owners:
            owners.append(path.name)


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


def _installed_root(plugin_name: str) -> Path | None:
    """已装插件的本地落点，按模块名推断；推断不出返回 None。"""
    service = ServiceTools.get_service(plugin_name)
    module_name = getattr(service, "module_name", "") or ""
    if not module_name:
        return None
    base = Path(*module_name.split("."))
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    if base.is_dir():
        return base
    return None


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
    def target_path(self) -> Path:
        """插件在磁盘上的实际落点。"""
        if self.repo:
            return PLUGINS_DIR / _repo_name(self.repo)
        path = _to_plugin_path(self.path)
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


def _rollback_install(backup: Path | None, target: Path | None, existed: bool):
    """安装失败时把落点恢复原状。

    ``existed`` 表示安装前目标是否已存在：已存在且有备份就还原，全新安装
    则清掉半成品。目标本来存在、备份却失败时不敢动它——此刻盘上的可能
    仍是旧版本，删掉就真丢了。
    """
    if target is None:
        return
    if backup is not None and backup.exists():
        _restore_plugin_dir(backup, target)
        return
    if existed:
        log.warning(f"{target} 无可用备份，保持原状")
        return
    try:
        if target.is_dir():
            _safe_rmtree(target)
        elif target.exists():
            target.unlink()
    except OSError as e:
        log.error(f"清理安装残留 {target} 失败：{e}")


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

    # 单文件型插件没有自己的目录，也就没有 meta.yml 可查
    meta_path = root / "meta.yml"
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
        data = await request.get(PLUGINS_URL, timeout=INDEX_TIMEOUT)
        if data.status_code != 200:
            raise PluginError(f"插件索引拉取失败，状态码：{data.status_code}")
        try:
            raw = data.json()
        except ValueError as e:
            raise PluginError(f"插件索引解析失败：{e}") from e
        if not isinstance(raw, dict):
            raise PluginError("插件索引格式错误：顶层应是插件名到信息的映射")
        cls.plugin_list = {
            name: _normalize_entry(name, info if isinstance(info, dict) else {})
            for name, info in raw.items()
        }
        _save_index_cache(cls.plugin_list)

    @classmethod
    async def ensure_list(cls) -> dict:
        """确保列表可用；已有数据时不联网。

        索引只在手动刷新时才更新，日常读取走内存，避免每翻一页、每开一次
        WebUI 都去拉一次 GitHub。联网失败时退回上次落盘的索引，断网也有内容。
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
                cached = _load_index_cache()
                if cached:
                    log.info(f"改用上次落盘的插件索引，共 {len(cached)} 个插件")
                    cls.plugin_list = cached
        return cls.plugin_list

    @classmethod
    async def refresh_list(cls) -> dict:
        """强制联网刷新，供手动触发的场景使用。"""
        async with cls._lock():
            await cls.fresh_list()
        # 手动刷新意味着「我要看最新的」，适配版本与文件清单一并作废
        _meta_cache.clear()
        _meta_failures.clear()
        _tree_cache.clear()
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
        """取一条插件记录，仓库型插件以远端 ``meta.yml`` 为准。

        适配版本（framework / lkapi）必须看仓库当前声明的：索引里的可能已经
        落后于仓库实际内容，按索引判兼容性，等于拿旧版本的要求去衡量新版本
        的代码。远端拿不到时退回本地 meta.yml，再不行才用索引。
        """
        if plugin_name not in cls.plugin_list:
            raise PluginError(f"找不到插件 {plugin_name}")
        raw = cls.plugin_list[plugin_name]
        entry = PluginEntry.from_mapping(plugin_name, raw)
        if not entry.repo:
            # 非仓库型没有远端 meta；已装的本地那份同样能补 lkapi。
            meta = cls.read_local_meta(plugin_name, raw)
            if not meta:
                return entry
            return PluginEntry.from_mapping(
                plugin_name,
                {**raw, **{k: v for k, v in meta.items() if v is not None}},
                fallback_author=entry.author,
            )
        meta = _meta_cached(entry.repo)
        if meta is None and not _meta_in_cooldown(entry.repo):
            try:
                meta = await cls.get_github_plugin_meta(entry.repo)
            except Exception as e:
                log.warning(f"获取插件`{plugin_name}`的远端 meta.yml 失败：{e}")
                _meta_failures[entry.repo] = time.time()
                meta = {}
            if isinstance(meta, dict) and meta:
                _meta_cache[entry.repo] = (time.time(), meta)
                _meta_failures.pop(entry.repo, None)
        if not isinstance(meta, dict) or not meta:
            meta = cls.read_local_meta(plugin_name, raw)
        if not meta:
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
        """批量取插件记录，单条失败不影响其它条目。

        并发取：逐条串行时，远端慢一次就要等一轮，插件多了列表直接拖死。
        结果顺序与 ``names`` 一致。
        """
        if names is None:
            names = list(cls.plugin_list)
        wanted = list(names)

        async def fetch(name: str) -> PluginEntry | None:
            try:
                return await cls.get_entry(name)
            except PluginError:
                return None

        results = await asyncio.gather(*(fetch(name) for name in wanted))
        return [entry for entry in results if entry is not None]

    @classmethod
    def compat(cls, entry: PluginEntry) -> CompatResult:
        """插件要求与当前环境的比对结果（仅用于标注，不拦截安装）。"""
        return check_compat(entry.requirement)

    @classmethod
    async def install_plugin(cls, plugin_name: str, load: bool = False):
        if plugin_name not in cls.plugin_list:
            log.info(f"安装未知插件`{plugin_name}`")
            raise PluginError(f"找不到插件 {plugin_name}")
        entry = await cls.get_entry(plugin_name)
        compat = cls.compat(entry)
        if not compat.ok:
            log.warning(f"插件`{plugin_name}`与当前环境不匹配：{compat.reason}")
        progress.start(plugin_name, "install")
        target: Path | None = None
        backup: Path | None = None
        existed = False
        try:
            target = entry.target_path
            existed = target.exists()
            if existed:
                backup = _backup_plugin_dir(target)
        except PluginError:
            target = None
        try:
            async with _install_lock_for(plugin_name), _global_install_gate():
                await cls._install_plugin(plugin_name, entry, load)
        except Exception as e:
            _rollback_install(backup, target, existed)
            progress.finish(plugin_name, ok=False, message=str(e))
            raise
        else:
            _drop_backup(backup)
            _forget_update_state(plugin_name)
        finally:
            _install_locks.pop(plugin_name, None)
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
                    file_list = await cls._collect_download_files(res, is_dir=True)
                    await cls.download_github_file(file_list)
        except Exception as e:
            log.warning(f"插件`{plugin_name}`资源安装失败:发生错误{e}")
            raise PluginError(f"插件资源安装失败:{e}")
        path: str = entry.path
        p_path = path.replace(".", "/")
        try:
            log.debug(f"开始下载插件`{plugin_name}`")
            progress.update(plugin_name, "fetch", path)
            target_rel = p_path if entry.is_dir else f"{p_path}.py"
            file_list = await cls._collect_download_files(target_rel, entry.is_dir)
            await cls.download_github_file(file_list)
            target = Path(".") / target_rel
            progress.update(plugin_name, "validate")
            problems = _validate_plugin_files(target, entry.is_dir)
            if problems:
                for problem in problems:
                    log.warning(f"插件`{plugin_name}`校验未通过：{problem}")
                raise PluginError(f"插件校验未通过：{'；'.join(problems)}")
            req_path = target / "requirements.txt"
            if entry.is_dir and req_path.exists():
                log.info(f"开始为`{plugin_name}`安装依赖")
                progress.update(plugin_name, "deps")
                stdout, _ = await _run_command(_pip_install_args(req_path))
                log.debug(f"`{plugin_name}`依赖安装信息: stdout={stdout}")
                _register_requirements(target)
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
        # 索引只补元信息；插件下架或索引拉取失败时仍要能卸，按本地落点反推
        raw = cls.plugin_list.get(plugin_name) or {}
        entry = PluginEntry.from_mapping(plugin_name, raw)
        if not entry.repo and not entry.path:
            local_root = _installed_root(plugin_name)
            if local_root is None:
                raise PluginError(f"无法定位 {plugin_name} 的安装位置，请手动卸载")
            entry = PluginEntry.from_mapping(
                plugin_name, {"path": str(local_root), "is_dir": local_root.is_dir()}
            )
        progress.start(plugin_name, "remove", "cleanup")
        try:
            async with _install_lock_for(plugin_name), _global_install_gate():
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
        finally:
            _install_locks.pop(plugin_name, None)
        progress.finish(plugin_name, ok=True, message="移除完成")
        _forget_update_state(plugin_name)
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
                _register_requirements(target_path)
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
                    _register_requirements(target_path)
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
            has_update = not _same_version(local_version, remote_version)
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
        """取仓库远端 ``meta.yml``。

        ``main`` 与 ``master`` 都试：仓库默认分支不统一，只认 main 会让一部分
        插件永远取不到适配版本，进而被误判。
        """
        if not repo.startswith("https://github.com/"):
            raise PluginError(f"不支持的仓库类型：{repo}")
        repo_path = repo.replace("https://github.com/", "").rstrip("/")
        last_status = None
        for branch in ("main", "master"):
            url = f"https://raw.githubusercontent.com/{repo_path}/{branch}/meta.yml"
            response = await request.get(
                url, follow_redirects=True, timeout=META_TIMEOUT
            )
            if response.status_code == 200:
                meta = yaml.safe_load(response.text)
                return meta if isinstance(meta, dict) else {}
            last_status = response.status_code
        raise PluginError(f"无法获取远程 meta.yml，状态码：{last_status}")

    @staticmethod
    def _download_target(raw_path: str) -> Path:
        """把索引给的相对路径钉在允许的落点内。

        ``path`` 来自远端响应，属于外部输入；``../../config.yml`` 这种值会被
        ``Path(".") / path`` 原样拼出去，写到工作目录之外。

        允许 ``plugins/`` 与 ``res/`` 两个根：前者是插件本体，后者是插件随包
        携带的资源（``res`` 字段），两者都可能在索引里登记。
        """
        text = str(raw_path or "").strip().replace("\\", "/")
        if not text or text.startswith("/") or (len(text) > 1 and text[1] == ":"):
            raise PluginError(f"插件文件路径非法：{raw_path!r}")
        target = (Path(".") / text).resolve()
        for root in (PLUGINS_DIR, RES_DIR):
            try:
                target.relative_to(root.resolve())
            except ValueError:
                continue
            return target
        raise PluginError(f"插件文件路径越界：{raw_path!r}")

    @classmethod
    async def _repo_tree(cls) -> dict[str, str]:
        """仓库文件清单：仓库内路径 → blob sha。带缓存，装多个插件只列一次。"""
        cached = _tree_cache.get("tree")
        if cached is not None:
            stamped, tree = cached
            if time.time() - stamped <= TREE_CACHE_TTL:
                return tree
        data = await request.get(TREES_URL, timeout=INDEX_TIMEOUT)
        if data.status_code != 200:
            raise PluginError(f"拉取仓库文件清单失败，状态码：{data.status_code}")
        payload = data.json()
        entries = payload.get("tree") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            raise PluginError("仓库文件清单格式错误：缺少 tree")
        tree = {
            entry["path"]: str(entry.get("sha") or "")
            for entry in entries
            if isinstance(entry, dict)
            and entry.get("type") == "blob"
            and entry.get("path")
        }
        if not tree:
            raise PluginError("仓库文件清单为空")
        _tree_cache["tree"] = (time.time(), tree)
        return tree

    @staticmethod
    def _tree_matches(tree: dict[str, str], target: str, is_dir: bool) -> list:
        """从清单里取出一个登记项对应的全部文件，返回 ``(仓库路径, sha)``。"""
        target = target.rstrip("/")
        # 精确同名先判：res 登记项不区分文件/目录，单个文件也要能命中
        if target in tree:
            return [(target, tree[target])]
        prefix = f"{target}/"
        hits = [(path, sha) for path, sha in tree.items() if path.startswith(prefix)]
        if hits or is_dir:
            return hits
        candidate = f"{target}.py"
        if candidate in tree:
            return [(candidate, tree[candidate])]
        return []

    @classmethod
    async def _collect_download_files(cls, target: str, is_dir: bool) -> list[dict]:
        """把登记项展开成待下载文件列表。"""
        tree = await cls._repo_tree()
        matches = cls._tree_matches(tree, target, is_dir)
        if not matches:
            raise PluginError(f"仓库里找不到 {target!r}")
        return [
            {
                "path": path,
                "sha": sha,
                "download_url": RAW_URL.format(path),
            }
            for path, sha in matches
        ]

    @staticmethod
    async def download_github_file(file_list):
        """
        下载 GitHub 文件
        :param file_list: 下载保存路径列表
        :return: None
        """
        for file in file_list:
            if file.get("type") == "dir" or not file.get("download_url"):
                raise PluginError(f"下载条目缺少文件地址：{file.get('path')!r}")
            file_path = PluginManager._download_target(file["path"])
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
    def cached_update_state(cls, plugin_name: str) -> tuple[str, str] | None:
        """上次检查到的更新；列表页只读它，不联网。"""
        return cached_update_state(plugin_name)

    @classmethod
    async def check_update(cls, plugin_name: str):
        state = await cls._check_update_uncached(plugin_name)
        _remember_update_state(plugin_name, state)
        return state

    @classmethod
    async def _check_update_uncached(cls, plugin_name: str):
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
    async def check_updates(cls, names: list[str] | None = None) -> dict:
        """批量检查更新，结果同时写进缓存。

        并发但带总预算：单个插件的远端请求可能很慢，串行会把整个请求拖死，
        无限并发又会被最慢的那个卡住。超时的条目按「未知」返回（值为 None）。
        """
        if names is None:
            names = [n for n in cls.plugin_list if n in ServiceTools.service_list]
        wanted = list(names)

        async def one(name: str):
            try:
                return name, await cls.check_update(name)
            except Exception as e:
                log.debug(f"检查插件`{name}`更新出错：{e}")
                return name, None

        tasks = [asyncio.create_task(one(name)) for name in wanted]
        if not tasks:
            return {}
        done, pending = await asyncio.wait(tasks, timeout=UPDATE_CHECK_BUDGET)
        for task in pending:
            task.cancel()
        result = dict.fromkeys(wanted)
        for task in done:
            with contextlib.suppress(Exception):
                name, state = task.result()
                result[name] = state
        if pending:
            log.warning(f"检查更新超时，{len(pending)} 个插件按未知处理")
        return result

    @classmethod
    async def update_plugin(cls, plugin_name: str, force: bool = False):
        """更新一个插件。

        ``force=True`` 时跳过「是否已有新版」的判断直接重装：用于版本号没变
        但内容需要修复、或上次更新不完整的情况。WebUI 的「强制更新」走这里。
        """
        if plugin_name not in cls.plugin_list:
            return False, f"找不到插件 {plugin_name}"
        entry = await cls.get_entry(plugin_name)
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
            async with _install_lock_for(plugin_name), _global_install_gate():
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
            _forget_update_state(plugin_name)
        finally:
            _install_locks.pop(plugin_name, None)

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
