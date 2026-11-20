from __future__ import annotations

import socket
from collections.abc import Callable
from types import ModuleType

import httpx
import nonebot.adapters.qq.adapter as qq_adapter
import nonebot.adapters.qq.bot as qq_bot
import nonebot.adapters.qq.utils as qq_utils
from nonebot.adapters.qq.exception import NetworkError
from nonebot.exception import WebSocketClosed
from nonebot.utils import escape_tag

_CONNECTION_ERRORS = (
    WebSocketClosed,
    NetworkError,
    TimeoutError,
    OSError,
    httpx.RequestError,
)
"""连接类异常。其余异常仍然保留完整调用栈"""

_MAX_DETAIL_LENGTH = 120

_HINT_RESOLVE_FAILED = "域名解析失败，检查容器 DNS 与网络"
_HINT_GATEWAY_FAILED = "启动阶段取网关失败，QQ 适配器不会自动重连，需重启"

_PATCHED_FLAG = "_atri_simplified"


def _describe(exception: BaseException) -> str:
    """拼出异常的单行摘要，并附上来源异常里信息量最大的那句。"""
    if isinstance(exception, WebSocketClosed):
        brief = f"WebSocketClosed(code={exception.code})"
        if exception.reason:
            brief += f" reason={exception.reason}"
    elif isinstance(exception, NetworkError):
        brief = f"NetworkError({exception.msg or 'request failed'})"
    else:
        brief = f"{type(exception).__name__}: {exception}"

    detail = ""
    cause = exception.__cause__
    while cause is not None:
        text = str(cause).strip()
        if text and text not in brief:
            if len(text) > _MAX_DETAIL_LENGTH:
                text = text[:_MAX_DETAIL_LENGTH] + "..."
            detail = f" <- {text}"
            break
        cause = cause.__cause__

    return brief + detail


def _hint(exception: BaseException, message: str) -> str:
    """给常见连接故障补一句排查方向。"""
    hints: list[str] = []
    current: BaseException | None = exception
    while current is not None:
        if isinstance(current, socket.gaierror):
            hints.append(_HINT_RESOLVE_FAILED)
            break
        current = current.__cause__
    if "Failed to get gateway info" in message:
        hints.append(_HINT_GATEWAY_FAILED)
    return "；".join(hints)


def _simplified_log(original: Callable[..., None]) -> Callable[..., None]:
    def log(level: str, message: str, exception: Exception | None = None) -> None:
        if exception is not None and isinstance(exception, _CONNECTION_ERRORS):
            text = f"{message} <y>{escape_tag(_describe(exception))}</y>"
            if hint := _hint(exception, message):
                text += f" <y>{escape_tag(hint)}</y>"
            original(level, text)
            return
        original(level, message, exception)

    setattr(log, _PATCHED_FLAG, True)
    return log


def _patch(module: ModuleType) -> None:
    original = module.log
    if getattr(original, _PATCHED_FLAG, False):
        return
    module.log = _simplified_log(original)


def install() -> None:
    """替换 QQ 适配器各处日志器；重复调用无副作用。"""
    for module in (qq_utils, qq_adapter, qq_bot):
        _patch(module)


install()
