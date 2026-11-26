import asyncio
import contextlib
import hashlib
import os
import time

import anyio
import httpx

from ATRI.exceptions import PluginError
from ATRI.log import log
from ATRI.utils import request

from . import state
from .paths import download_target


async def repo_tree(cls) -> dict[str, str]:
    """仓库文件清单：仓库内路径 → blob sha。带缓存，装多个插件只列一次。"""
    cached = state._tree_cache.get("tree")
    if cached is not None:
        stamped, tree = cached
        if time.time() - stamped <= state.TREE_CACHE_TTL:
            return tree
    data = await request.get(state.TREES_URL, timeout=state.INDEX_TIMEOUT)
    if data.status_code != 200:
        raise PluginError(f"拉取仓库文件清单失败，状态码：{data.status_code}")
    payload = data.json()
    entries = payload.get("tree") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise PluginError("仓库文件清单格式错误：缺少 tree")
    tree = {
        entry["path"]: str(entry.get("sha") or "")
        for entry in entries
        if isinstance(entry, dict) and entry.get("type") == "blob" and entry.get("path")
    }
    if not tree:
        raise PluginError("仓库文件清单为空")
    state._tree_cache["tree"] = (time.time(), tree)
    return tree


def tree_matches(tree: dict[str, str], target: str, is_dir: bool) -> list:
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


async def collect_download_files(cls, target: str, is_dir: bool) -> list[dict]:
    """把登记项展开成待下载文件列表。"""
    tree = await cls._repo_tree()
    matches = cls._tree_matches(tree, target, is_dir)
    if not matches:
        raise PluginError(f"仓库里找不到 {target!r}")
    return [
        {
            "path": path,
            "sha": sha,
            "download_url": state.RAW_URL.format(path),
        }
        for path, sha in matches
    ]


async def download_github_file(cls, file_list, allowed_root=None):
    """下载 GitHub 文件，带 sha 校验与重试。

    ``allowed_root`` 给出时把落点再收窄到该插件自己的目录/资源子目录，
    避免一个插件的索引条目把文件写进别的插件或机器人本体资源里。
    """
    for file in file_list:
        if file.get("type") == "dir" or not file.get("download_url"):
            raise PluginError(f"下载条目缺少文件地址：{file.get('path')!r}")
        file_path = download_target(file["path"], allowed_root)
        temporary_path = file_path.with_name(f".{file_path.name}.part")
        file_path.parent.mkdir(parents=True, exist_ok=True)
        last_error = None
        for attempt in range(1, state.DOWNLOAD_RETRIES + 1):
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
                with contextlib.suppress(OSError):
                    temporary_path.unlink(missing_ok=True)
                # 落点被占用（Windows 共享冲突）重试也没用，直接报清楚原因
                if isinstance(e, PermissionError):
                    raise PluginError(
                        f"写入 {file_path} 失败：文件被占用，请关闭后重试"
                    ) from e
                if attempt < state.DOWNLOAD_RETRIES:
                    log.warning(f"下载文件`{file_path}`失败，第 {attempt} 次重试: {e}")
                    await asyncio.sleep(state.DOWNLOAD_RETRY_DELAY)
                else:
                    raise PluginError(
                        f"下载文件失败（重试 {state.DOWNLOAD_RETRIES} 次）: "
                        f"{file_path}: {last_error}"
                    ) from e
