import asyncio
import contextlib

import yaml

from ATRI import service
from ATRI.exceptions import PluginError
from ATRI.log import log

from . import state, versions
from .entries import PluginEntry, repo_name


async def check_github_plugin_update(cls, repo: str) -> dict:
    """检查仓库型插件是否有更新。

    :return: ``{'has_update': bool, 'local_version': str, 'remote_version': str}``
    """
    log.debug(f"开始检查 GitHub 插件更新：{repo}")
    try:
        name = repo_name(repo)
        target_path = state.PLUGINS_DIR / name
        if not target_path.exists():
            raise PluginError(f"插件目录 {target_path} 不存在")
        local_meta_path = target_path / "meta.yml"
        if not local_meta_path.exists():
            raise PluginError("未找到本地 meta.yml 文件")
        local_meta = yaml.safe_load(local_meta_path.read_text(encoding="utf-8")) or {}
        local_version = local_meta.get("version", "unknown")
        remote_meta = await cls.get_github_plugin_meta(repo)
        remote_version = (remote_meta or {}).get("version", "unknown")
        has_update = not versions.same_version(local_version, remote_version)
        result = {
            "has_update": has_update,
            "local_version": local_version,
            "remote_version": remote_version,
            "repo_name": name,
        }
        if has_update:
            log.debug(f"发现新版本：{name} {local_version} -> {remote_version}")
        return result
    except yaml.YAMLError as e:
        log.error(f"解析 meta.yml 失败：{e}")
        raise PluginError(f"解析 meta.yml 失败：{e}")
    except PluginError:
        raise
    except Exception as e:
        log.error(f"检查 GitHub 插件更新失败：{e}")
        raise PluginError(f"检查 GitHub 插件更新失败：{e}")


async def check_update(cls, plugin_name: str):
    state_now = await cls._check_update_uncached(plugin_name)
    state.remember_update_state(plugin_name, state_now)
    return state_now


async def check_update_uncached(cls, plugin_name: str):
    if plugin_name not in cls.plugin_list:
        return None
    entry = PluginEntry.from_mapping(plugin_name, cls.plugin_list[plugin_name])
    version = entry.version
    if plugin_name in service.ServiceTools.service_list:
        if version == "github":
            try:
                r = await cls.check_github_plugin_update(entry.repo)
            except PluginError as e:
                log.warning(f"插件`{plugin_name}`检查更新失败：{e.prompt}")
                return None
            if r["has_update"]:
                return r["local_version"], r["remote_version"]
            return None
        now_version = service.ServiceTools(plugin_name).load_service().version
        if not versions.same_version(now_version, version):
            return now_version, version
    return None


async def check_updates(cls, names: list[str] | None = None) -> dict:
    """批量检查更新，结果同时写进缓存。

    并发但带总预算：单个插件的远端请求可能很慢，串行会把整个请求拖死，
    无限并发又会被最慢的那个卡住。超时的条目按「未知」返回（值为 None）。
    """
    if names is None:
        names = [n for n in cls.plugin_list if n in service.ServiceTools.service_list]
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
    done, pending = await asyncio.wait(tasks, timeout=state.UPDATE_CHECK_BUDGET)
    for task in pending:
        task.cancel()
    result = dict.fromkeys(wanted)
    for task in done:
        with contextlib.suppress(Exception):
            name, state_now = task.result()
            result[name] = state_now
    if pending:
        log.warning(f"检查更新超时，{len(pending)} 个插件按未知处理")
    return result
