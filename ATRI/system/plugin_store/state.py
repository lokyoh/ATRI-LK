import json
import os
import time
from pathlib import Path

import anyio

from ATRI.log import log

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

#: 单次 pip / git 子进程的超时秒数。卡死的子进程会让全局安装锁永不释放。
DEP_TIMEOUT = 300.0

#: 索引拉取超时。索引只有几 KB，远端挂住时没必要等满全局的 30s。
INDEX_TIMEOUT = 8.0

#: 远端 meta 拉取失败后的冷却秒数。失败本身不进正缓存（断网恢复要能立刻拉到），
#: 但要挡住「每次打开页面都把 N 个仓库重试一遍」。
META_FAILURE_COOLDOWN = 60.0
_meta_failures: dict[str, float] = {}

#: 索引落盘位置。断网启动时商店仍有内容，不用整页空白。
INDEX_CACHE_FILE = Path(".") / "data" / "plugin_store" / "index.json"
INDEX_CACHE_VERSION = 1

_install_locks: dict[str, tuple[anyio.Lock, int]] = {}
"""按插件名区分的互斥锁，避免并发写同一个插件目录。

用 ``anyio.Lock`` 而不是 ``asyncio.Lock``：后者一旦发生过竞争就会绑定到
当时的 event loop，换一个 loop 再获取会抛「bound to a different event loop」
（测试里每次 ``asyncio.run`` 都是一个新 loop，会直接踩到）。

不按目录加锁是按插件名：同名插件必然落在同一目录，不同插件互不阻塞。
**带引用计数**：直接在 finally 里 pop 会让仍在排队的协程握着孤儿锁，
后来者拿到新锁，互斥就名存实亡。
"""

_install_gate: anyio.Lock | None = None


def meta_cached(repo: str) -> dict | None:
    item = _meta_cache.get(repo)
    if item is None:
        return None
    stamped, meta = item
    if time.time() - stamped > META_CACHE_TTL:
        _meta_cache.pop(repo, None)
        return None
    return meta


def remember_meta(repo: str, meta: dict):
    _meta_cache[repo] = (time.time(), meta)
    _meta_failures.pop(repo, None)


def meta_in_cooldown(repo: str) -> bool:
    stamped = _meta_failures.get(repo)
    if stamped is None:
        return False
    if time.time() - stamped > META_FAILURE_COOLDOWN:
        _meta_failures.pop(repo, None)
        return False
    return True


def remember_meta_failure(repo: str):
    _meta_failures[repo] = time.time()


def remember_update_state(plugin_name: str, state: tuple[str, str] | None):
    _update_cache[plugin_name] = (time.time(), state)


def forget_update_state(plugin_name: str):
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


def install_lock_for(plugin_name: str) -> anyio.Lock:
    item = _install_locks.get(plugin_name)
    if item is None:
        lock = anyio.Lock()
        _install_locks[plugin_name] = (lock, 1)
        return lock
    lock, refs = item
    _install_locks[plugin_name] = (lock, refs + 1)
    return lock


def release_install_lock(plugin_name: str):
    """归还一次引用；归零才真正移除那把锁。"""
    item = _install_locks.get(plugin_name)
    if item is None:
        return
    lock, refs = item
    if refs <= 1:
        _install_locks.pop(plugin_name, None)
    else:
        _install_locks[plugin_name] = (lock, refs - 1)


def global_install_gate() -> anyio.Lock:
    """全局安装互斥：装依赖与改 ``sys.modules`` 不能并行。"""
    global _install_gate
    if _install_gate is None:
        _install_gate = anyio.Lock()
    return _install_gate


def save_index_cache(plugins: dict):
    """把索引写到本地。写失败只记日志：缓存是锦上添花，不该影响刷新本身。"""
    payload = {
        "version": INDEX_CACHE_VERSION,
        "saved_at": time.time(),
        "plugins": plugins,
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


def load_index_cache() -> dict:
    """读上次落盘的索引；不存在、损坏或版本不符都返回空字典。"""
    from .entries import normalize_entry

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
        name: normalize_entry(name, info if isinstance(info, dict) else {})
        for name, info in plugins.items()
    }


def clear_derived_caches():
    """手动刷新索引后作废所有由索引派生的缓存。"""
    _meta_cache.clear()
    _meta_failures.clear()
    _tree_cache.clear()
