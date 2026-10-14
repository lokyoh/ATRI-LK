"""Filesystem layout for the lkpet plugin.

Static definitions live under ``res/data/lkpet``; runtime caches and logs live
under ``data/plugins/lkpet``.  Both roots are derived from ``ATRI.dir`` so they
follow the framework's working-directory-relative convention instead of being
hard-coded per plugin.
"""

from pathlib import Path

from ATRI.dir import PLUGIN_DATA_DIR, RES_DATA_DIR

STATIC_DATA_ROOT: Path = RES_DATA_DIR / "lkpet"
RUNTIME_DATA_ROOT: Path = PLUGIN_DATA_DIR / "lkpet"
CONFIG_DIR: Path = STATIC_DATA_ROOT / "config"
USERS_DIR: Path = RUNTIME_DATA_ROOT / "users"
LOGS_DIR: Path = RUNTIME_DATA_ROOT / "logs"

_DIRECTORIES: tuple[Path, ...] = (
    STATIC_DATA_ROOT,
    CONFIG_DIR,
    RUNTIME_DATA_ROOT,
    USERS_DIR,
    LOGS_DIR,
)


def ensure_dirs() -> None:
    """Create every lkpet data directory that may be missing."""
    for directory in _DIRECTORIES:
        directory.mkdir(parents=True, exist_ok=True)


def get_config_path(name: str) -> Path:
    """Return the YAML path for the config named ``name``."""
    return CONFIG_DIR / f"{name}.yaml"


def get_user_path(player_id: int) -> Path:
    """Return the optional runtime cache path for ``player_id``."""
    return USERS_DIR / f"{player_id}.json"
