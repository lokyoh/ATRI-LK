import asyncio
from collections.abc import Mapping

import yaml

from ATRI.exceptions import PluginError
from ATRI.log import log
from ATRI.utils import request

from . import state
from .entries import PluginEntry


async def fetch_remote_meta(repo: str) -> dict:
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
            url, follow_redirects=True, timeout=state.META_TIMEOUT
        )
        if response.status_code == 200:
            meta = yaml.safe_load(response.text)
            return meta if isinstance(meta, dict) else {}
        last_status = response.status_code
    raise PluginError(f"无法获取远程 meta.yml，状态码：{last_status}")


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


async def resolve_entry(cls, plugin_name: str) -> PluginEntry:
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
    meta = state.meta_cached(entry.repo)
    if meta is None and not state.meta_in_cooldown(entry.repo):
        try:
            meta = await cls.get_github_plugin_meta(entry.repo)
        except Exception as e:
            log.warning(f"获取插件`{plugin_name}`的远端 meta.yml 失败：{e}")
            state.remember_meta_failure(entry.repo)
            meta = {}
        if isinstance(meta, dict) and meta:
            state.remember_meta(entry.repo, meta)
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


async def resolve_entries(cls, names: list[str] | None = None) -> list[PluginEntry]:
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
