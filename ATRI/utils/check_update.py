import time
from datetime import UTC, datetime
from typing import ClassVar

import pytz

from ATRI.log import log

from . import request

REPO_RELEASE_URL = "https://api.github.com/repos/lokyoh/ATRI-LK/releases"

CACHE_TTL = 600
"""发布信息缓存秒数。

GitHub API 未认证时限流较紧，页首每次刷新页面都查会很快耗尽配额。
"""


class ReleaseInfo:
    def __init__(self, release_data):
        self.version = release_data["name"]
        self.tag_name = release_data.get("tag_name") or self.version
        update_time = release_data["updated_at"]
        update_time = update_time.replace("Z", "")
        utc_datetime = datetime.fromisoformat(update_time).replace(tzinfo=UTC)
        shanghai_datetime = utc_datetime.astimezone(pytz.timezone("Asia/Shanghai"))
        self.update_time = shanghai_datetime.strftime("%Y-%m-%d %H:%M")
        self.info = release_data["body"]


class CheckUpdate:
    _cached: ReleaseInfo | None = None
    _cached_at: float = 0.0
    _commit_cache: ClassVar[dict[str, tuple[str, float]]] = {}

    @staticmethod
    async def _get_release_info() -> dict:
        req = await request.get(REPO_RELEASE_URL)
        return req.json()

    @classmethod
    async def get_latest_info(cls, force: bool = False) -> ReleaseInfo | None:
        """取最新发布信息，默认走缓存。

        拉取失败时沿用上一次成功的结果：网络抖动不该让页首的提示突然消失。
        """
        now = time.time()
        if not force and cls._cached is not None and now - cls._cached_at < CACHE_TTL:
            return cls._cached
        try:
            data = await cls._get_release_info()
        except Exception:
            log.error("获取发布列表失败...")
            return cls._cached
        try:
            release_data: dict = data[0]
        except Exception:
            log.error("GitHub 数据结构已更改, 请前往仓库提交 Issue.")
            return cls._cached
        info = ReleaseInfo(release_data)
        cls._cached = info
        cls._cached_at = now
        return info

    @classmethod
    async def get_latest_commit(cls, branch: str, force: bool = False) -> str | None:
        """取远程分支最新提交；失败时沿用该分支上一次成功的结果。"""
        now = time.time()
        cached = cls._commit_cache.get(branch)
        if not force and cached is not None and now - cached[1] < CACHE_TTL:
            return cached[0]

        from . import git

        result = await git.run_git(
            "ls-remote", "origin", f"refs/heads/{branch}", cwd="."
        )
        if not result.ok:
            log.error(f"获取远程 {branch} 分支提交失败：{result.detail}")
            return cached[0] if cached is not None else None
        try:
            commit, ref = result.stdout.strip().split()
        except ValueError:
            log.error(f"远程 {branch} 分支提交数据无效")
            return cached[0] if cached is not None else None
        if ref != f"refs/heads/{branch}" or not commit:
            log.error(f"远程 {branch} 分支不存在")
            return cached[0] if cached is not None else None

        cls._commit_cache[branch] = (commit, now)
        return commit

    @classmethod
    def clear_cache(cls):
        cls._cached = None
        cls._cached_at = 0.0
        cls._commit_cache.clear()


def get_version_num(v: str) -> int:
    try:
        return int(v.replace("Release", "Patch0").replace("Patch", ""))
    except ValueError:
        return 0


def is_newer_version(
    latest_version: str, current_version: str, current_sub_version: str
) -> bool:
    if not latest_version:
        return False
    latest_main_version = latest_version[:11]
    if latest_main_version > current_version:
        return True
    if latest_main_version != current_version:
        return False
    if latest_version == f"{current_version} {current_sub_version}":
        return False
    if current_sub_version[:3] == "Pre":
        return True
    return get_version_num(latest_version[12:]) > get_version_num(current_sub_version)
