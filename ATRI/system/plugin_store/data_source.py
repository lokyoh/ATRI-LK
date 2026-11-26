from collections.abc import Mapping
from typing import ClassVar

import anyio

from ATRI.exceptions import PluginError as PluginError
from ATRI.log import log as log
from ATRI.utils import request as request

from . import (
    deps,
    entries,
    fetch,
    index,
    installer,
    meta,
    paths,
    state,
    transaction,
    updates,
    versions,
)
from . import progress as progress
from . import runtime as runtime_mod
from .compat import CompatResult
from .compat import check as check_compat

PluginEntry = entries.PluginEntry
PluginRuntimeManager = runtime_mod.PluginRuntimeManager

_same_version = versions.same_version


class PluginManager:
    plugin_list: ClassVar[dict] = {}
    """索引条目（插件名 → 归一化后的 dict），由 ``fresh_list`` 填充。"""

    _fetch_lock: ClassVar[anyio.Lock | None] = None

    @classmethod
    def _lock(cls) -> anyio.Lock:
        return index.lock_for(cls)

    @classmethod
    async def fresh_list(cls):
        return await index.fresh_list(cls)

    @classmethod
    async def ensure_list(cls) -> dict:
        return await index.ensure_list(cls)

    @classmethod
    async def refresh_list(cls) -> dict:
        return await index.refresh_list(cls)

    @classmethod
    async def get_plugin_list(cls) -> dict:
        return await index.get_plugin_list(cls)

    @classmethod
    async def check_list(cls):
        return await index.check_list(cls)

    @staticmethod
    def read_local_meta(plugin_name: str, raw: Mapping) -> dict:
        return meta.read_local_meta(plugin_name, raw)

    @classmethod
    async def get_entry(cls, plugin_name: str) -> PluginEntry:
        return await meta.resolve_entry(cls, plugin_name)

    @classmethod
    async def get_entries(cls, names: list[str] | None = None) -> list[PluginEntry]:
        return await meta.resolve_entries(cls, names)

    @classmethod
    def compat(cls, entry: PluginEntry) -> CompatResult:
        return check_compat(entry.requirement)

    @classmethod
    async def install_plugin(cls, plugin_name: str, load: bool = False):
        return await installer.install_plugin(cls, plugin_name, load)

    @classmethod
    async def _install_plugin(cls, plugin_name: str, entry: PluginEntry, load: bool):
        return await installer.install_entry(cls, plugin_name, entry, load)

    @classmethod
    async def remove_plugin(cls, plugin_name: str):
        return await installer.remove_plugin(cls, plugin_name)

    @classmethod
    async def update_plugin(cls, plugin_name: str, force: bool = False):
        return await installer.update_plugin(cls, plugin_name, force)

    @classmethod
    async def install_github_plugin(cls, repo: str) -> str:
        return await installer.install_github_plugin(cls, repo)

    @classmethod
    async def remove_github_plugin(cls, repo: str):
        return await installer.remove_github_plugin(cls, repo)

    @classmethod
    async def update_github_plugin(cls, repo: str):
        return await installer.update_github_plugin(cls, repo)

    @staticmethod
    async def _git_head(repo_path):
        from ATRI.utils import git

        return await git.head_commit(repo_path)

    @classmethod
    async def _rollback_github_plugin(cls, target_path, commit: str):
        return await installer.rollback_github_plugin(cls, target_path, commit)

    @classmethod
    async def check_github_plugin_update(cls, repo: str) -> dict:
        return await updates.check_github_plugin_update(cls, repo)

    @classmethod
    async def get_github_plugin_meta(cls, repo: str) -> dict:
        return await meta.fetch_remote_meta(repo)

    @classmethod
    def cached_update_state(cls, plugin_name: str):
        return state.cached_update_state(plugin_name)

    @classmethod
    async def check_update(cls, plugin_name: str):
        return await updates.check_update(cls, plugin_name)

    @classmethod
    async def _check_update_uncached(cls, plugin_name: str):
        return await updates.check_update_uncached(cls, plugin_name)

    @classmethod
    async def check_updates(cls, names: list[str] | None = None) -> dict:
        return await updates.check_updates(cls, names)

    @staticmethod
    def _download_target(raw_path: str, allowed_root=None):
        return paths.download_target(raw_path, allowed_root)

    @staticmethod
    def _tree_matches(tree: dict[str, str], target: str, is_dir: bool) -> list:
        return fetch.tree_matches(tree, target, is_dir)

    @classmethod
    async def _repo_tree(cls) -> dict[str, str]:
        return await fetch.repo_tree(cls)

    @classmethod
    async def _collect_download_files(cls, target: str, is_dir: bool) -> list[dict]:
        return await fetch.collect_download_files(cls, target, is_dir)

    @classmethod
    async def download_github_file(cls, file_list, allowed_root=None):
        return await fetch.download_github_file(cls, file_list, allowed_root)

    @staticmethod
    def _installed_root(plugin_name: str):
        return deps.installed_root(plugin_name)

    @staticmethod
    def _remove_service_files(plugin_name: str):
        return transaction.remove_service_files(plugin_name)
