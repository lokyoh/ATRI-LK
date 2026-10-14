"""Access to the user-facing texts externalised in ``texts.yaml``.

Everything the player can read lives there, so wording can be changed without
touching code.  Lookups always carry a default: a missing key degrades to the
fallback instead of breaking a command.

**指令前缀**：模板与代码兜底文案里统一写字面前缀 ``/宠物``；出口处由
:func:`apply_command_start` 把它替换为 ``ATRI.bot.BotUtils.get_command_start()``
的实际返回值（配置的 command_start 可能是 ``/``、``！`` 等）。因此凡经本模块
输出的用户可见文案都自动跟随前缀，无需逐条改文案。
"""

from typing import Any

_MISSING = object()

_COMMAND_TOKENS = ("/宠物", "/帮助")
"""模板里出现的字面前缀；:func:`apply_command_start` 按当前前缀替换。"""


def command_start() -> str:
    """当前生效的指令前缀；框架未就绪时兜底为 ``/``。"""
    try:
        from ATRI.bot import BotUtils

        return BotUtils.get_command_start()
    except Exception:  # pragma: no cover - 兜底：导入期/框架异常不应让文案崩溃
        return "/"


def apply_command_start(message: str) -> str:
    """把文案中的字面 ``/宠物`` / ``/帮助`` 替换为实际指令前缀。

    前缀本身即 ``/`` 时原样返回，零开销；``command_start`` 为空串时输出
    无前缀的 ``宠物.xxx``，与 nonebot 的解析行为一致。
    """
    if not message or "/" not in message:
        return message
    prefix = command_start()
    if prefix == "/":
        return message
    for token in _COMMAND_TOKENS:
        if token in message:
            message = message.replace(token, f"{prefix}{token[1:]}")
    return message


def _lookup(path: str) -> Any:
    from ..config_loader import load_config

    try:
        node: Any = load_config("texts")
    except (FileNotFoundError, ValueError):
        # 文案缺失只应降级为默认值，不该让整个插件加载失败。
        return _MISSING

    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def text(path: str, default: str = "", **fields: Any) -> str:
    """Return the text at dotted ``path``, formatted with ``fields``.

    Falls back to ``default`` when the key is absent or holds a non-string, and
    to the raw template when a placeholder cannot be filled.  The result always
    carries the runtime command prefix (see :func:`apply_command_start`).
    """
    value = _lookup(path)
    if not isinstance(value, str):
        value = default
    try:
        return apply_command_start(value.format(**fields))
    except (KeyError, IndexError, ValueError):
        return apply_command_start(value)


def text_list(path: str, default: list[str] | None = None) -> list[str]:
    """Return the string list at dotted ``path``; ``default`` when absent."""
    value = _lookup(path)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    return list(default or [])
