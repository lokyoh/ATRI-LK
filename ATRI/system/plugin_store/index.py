import anyio

from ATRI.exceptions import PluginError
from ATRI.log import log
from ATRI.utils import request

from . import state
from .entries import normalize_entry


def lock_for(cls) -> anyio.Lock:
    if cls._fetch_lock is None:
        cls._fetch_lock = anyio.Lock()
    return cls._fetch_lock


async def fresh_list(cls):
    log.debug("更新插件列表信息")
    data = await request.get(state.PLUGINS_URL, timeout=state.INDEX_TIMEOUT)
    if data.status_code != 200:
        raise PluginError(f"插件索引拉取失败，状态码：{data.status_code}")
    try:
        raw = data.json()
    except ValueError as e:
        raise PluginError(f"插件索引解析失败：{e}") from e
    if not isinstance(raw, dict):
        raise PluginError("插件索引格式错误：顶层应是插件名到信息的映射")
    cls.plugin_list = {
        name: normalize_entry(name, info if isinstance(info, dict) else {})
        for name, info in raw.items()
    }
    state.save_index_cache(cls.plugin_list)


async def ensure_list(cls) -> dict:
    """确保列表可用；已有数据时不联网。

    索引只在手动刷新时才更新，日常读取走内存，避免每翻一页、每开一次
    WebUI 都去拉一次 GitHub。联网失败时退回上次落盘的索引，断网也有内容。
    """
    if cls.plugin_list:
        return cls.plugin_list
    async with lock_for(cls):
        if cls.plugin_list:
            return cls.plugin_list
        try:
            await cls.fresh_list()
        except Exception as e:
            log.warning(f"更新插件列表失败：{e}")
            cached = state.load_index_cache()
            if cached:
                log.info(f"改用上次落盘的插件索引，共 {len(cached)} 个插件")
                cls.plugin_list = cached
    return cls.plugin_list


async def refresh_list(cls) -> dict:
    """强制联网刷新，供手动触发的场景使用。"""
    async with lock_for(cls):
        await cls.fresh_list()
    # 手动刷新意味着「我要看最新的」，适配版本与文件清单一并作废
    state.clear_derived_caches()
    return cls.plugin_list


async def get_plugin_list(cls) -> dict:
    """兼容旧调用方：语义等同于 ``ensure_list``。"""
    return await cls.ensure_list()


async def check_list(cls):
    if not cls.plugin_list:
        await cls.ensure_list()
    if not cls.plugin_list:
        raise PluginError("更新列表失败")
