from typing import Literal

from pydantic import Field

from ATRI.dir import SYS_CONFIG_DIR
from ATRI.exceptions import ReadFileError, WriteFileError
from ATRI.utils.model import BaseModel

SETTINGS_FILE = SYS_CONFIG_DIR / "auth.json"

_cache: tuple[float, "AuthSettings"] | None = None


class AuthSettings(BaseModel):
    enabled: bool = True
    trace_buffer: int = 500
    deny_prompt: bool = False
    deny_prompt_cooldown: float = 5.0
    legacy_bans_migrated: bool = False

    malicious_enabled: bool = False
    malicious_window: float = 10.0
    malicious_max: int = 8
    malicious_ban_minutes: float = 10.0
    malicious_mode: Literal["off", "blacklist", "whitelist"] = "blacklist"
    malicious_services: list[str] = Field(default_factory=list)


def _write_default() -> None:
    try:
        AuthSettings().write_into_file(SETTINGS_FILE)
    except Exception as e:
        raise WriteFileError("Write auth settings failed") from e


def reset_cache() -> None:
    """清空设置缓存（测试或手工重载时使用）。"""
    global _cache
    _cache = None


def get_settings() -> AuthSettings:
    """读取鉴权设置，按文件 mtime 缓存。"""
    global _cache
    if not SETTINGS_FILE.is_file():
        _write_default()
    try:
        mtime = SETTINGS_FILE.stat().st_mtime
    except OSError as e:
        raise ReadFileError(f"无法读取鉴权设置文件：{SETTINGS_FILE}") from e
    if _cache is not None and _cache[0] == mtime:
        return _cache[1]
    settings = AuthSettings.read_from_file(SETTINGS_FILE)
    # trace_buffer 直接作为 deque 的 maxlen，做一次边界收敛
    settings.trace_buffer = max(1, min(settings.trace_buffer, 10000))
    _cache = (mtime, settings)
    return settings


def save_settings(settings: AuthSettings) -> None:
    """保存鉴权设置并让缓存失效。"""
    try:
        settings.write_into_file(SETTINGS_FILE)
    except Exception as e:
        raise WriteFileError("Write auth settings failed") from e
    reset_cache()
