"""YAML configuration loader for the lkpet plugin.

Every numeric value, text and event of the raising system is externalised under
``res/data/lkpet/config``; this module is the single entry point used by the
``core`` modules.
"""

from typing import Any

import yaml

from .utils.paths import ensure_dirs, get_config_path

_cache: dict[str, dict[str, Any]] = {}


def load_config(name: str, force_reload: bool = False) -> dict[str, Any]:
    """Load the YAML config named ``name``, caching the parsed mapping."""
    if not force_reload and name in _cache:
        return _cache[name]

    ensure_dirs()
    path = get_config_path(name)
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在：{path}")

    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)

    if not isinstance(data, dict):
        raise TypeError(f"配置文件格式错误（顶层应为映射）：{path}")

    _cache[name] = data
    return data


def reload_all() -> None:
    """Drop every cached config so the next load reads from disk."""
    _cache.clear()
