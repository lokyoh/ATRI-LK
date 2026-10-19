"""Agent/LLM 模型输入输出的独立日志。

- 目录：``data/logs/llm``，每次启动新建一轮运行目录，滚动保留最近 3 轮；
- 单个日志文件最大 10MB，超出后由 loguru 自动轮转；
- 主日志只保留一行调用摘要，完整的输入/输出仅写入本日志；
- 文本按“调用”分块记录，图片/音频等二进制内容只记录类型和长度，避免日志爆炸。
"""

from __future__ import annotations

import atexit
import contextlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from loguru import logger

from ATRI.exceptions import str_traceback
from ATRI.log import LLM_LOG_EXTRA, LOGGER_DIR, log
from ATRI.utils.datetime import now

from .contents import LLMContent, LLMContents, LLMMessages, Sys1Content

LLM_LOG_DIR = LOGGER_DIR / "llm"
"""LLM 输入输出日志根目录"""

MAX_LOG_SIZE = "10 MB"
"""单个日志文件上限"""

MAX_RUNS = 3
"""保留最近几次启动的日志"""

MAX_TEXT_CHARS = 100_000
"""单段文本最大记录字符数，超出部分会被截断"""

MAX_RECORD_CHARS = 500_000
"""单次调用记录的最大字符数，避免单条记录把日志文件顶过 10MB"""

_RUN_DIR_PATTERN = re.compile(r"^\d{8}-\d{6}(?:-\d+)?$")

_run_dir: Path | None = None
_sink_id: int | None = None
_initialized = False
_atexit_registered = False

_llm_logger = logger.bind(**{LLM_LOG_EXTRA: True})


def _prepare_run_dir(root: Path) -> Path:
    """创建本轮运行目录，并删除超过保留轮数的旧目录。"""
    root.mkdir(parents=True, exist_ok=True)
    base = now().strftime("%Y%m%d-%H%M%S")
    run_dir = root / base
    index = 1
    while run_dir.exists():
        run_dir = root / f"{base}-{index}"
        index += 1
    run_dir.mkdir()
    _prune_runs(root)
    return run_dir


def _prune_runs(root: Path) -> None:
    runs = sorted(
        (
            path
            for path in root.iterdir()
            if path.is_dir() and _RUN_DIR_PATTERN.match(path.name)
        ),
        key=lambda path: path.name,
    )
    for outdated in runs[:-MAX_RUNS]:
        shutil.rmtree(outdated, ignore_errors=True)


def init_llm_log(log_root: Path | None = None) -> Path | None:
    """初始化 LLM 日志，返回本轮日志目录；重复调用不会重复创建。"""
    global _atexit_registered, _initialized, _run_dir, _sink_id
    if _initialized:
        return _run_dir
    _initialized = True
    if not _atexit_registered:
        atexit.register(flush_llm_log)
        _atexit_registered = True
    try:
        _run_dir = _prepare_run_dir(log_root or LLM_LOG_DIR)
        _sink_id = logger.add(
            _run_dir / "llm.log",
            rotation=MAX_LOG_SIZE,
            enqueue=True,
            encoding="utf-8",
            format="{message}",
            filter=lambda record: bool(record["extra"].get(LLM_LOG_EXTRA)),
            level="INFO",
            backtrace=False,
            diagnose=False,
        )
    except Exception as exc:
        _run_dir = None
        _sink_id = None
        log.warning(f"LLM 日志初始化失败: {exc}")
    return _run_dir


def shutdown_llm_log() -> None:
    """移除 LLM 日志 sink（主要供测试使用）。"""
    global _initialized, _run_dir, _sink_id
    if _sink_id is not None:
        with contextlib.suppress(ValueError):
            logger.remove(_sink_id)
    _sink_id = None
    _run_dir = None
    _initialized = False


def flush_llm_log() -> None:
    """等待异步日志线程把已写入的内容落盘。"""
    with contextlib.suppress(Exception):
        logger.complete()


def _clip(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[已截断，共 {len(text)} 字符]"


def _render_part(part: LLMContent) -> str:
    if part.type == "text":
        return _clip(str(part.content))
    raw = str(part.content)
    return f"[{part.type}: {len(raw)} 字符，内容已省略]"


def _render_contents(content: LLMContents) -> str:
    return "\n".join(_render_part(part) for part in content.contents)


def _render_messages(messages: LLMMessages) -> str:
    return "\n".join(
        f"[{message.role}]\n{_render_contents(message.content)}"
        for message in messages.messages
    )


def _render_content(content: Any) -> str:
    if isinstance(content, str):
        return _clip(content)
    if isinstance(content, LLMContents):
        return _render_contents(content)
    if isinstance(content, LLMMessages):
        return _render_messages(content)
    if isinstance(content, Sys1Content):
        return _clip(
            json.dumps(content.to_dict(), ensure_ascii=False, indent=2, default=str)
        )
    if isinstance(content, list | tuple):
        return _clip(
            json.dumps(list(content), ensure_ascii=False, indent=2, default=str)
        )
    if isinstance(content, dict):
        return _clip(json.dumps(content, ensure_ascii=False, indent=2, default=str))
    if content is None:
        return ""
    return _clip(str(content))


def _render_response(content: Any) -> str:
    if isinstance(content, str):
        return _clip(content)
    if isinstance(content, Path):
        return str(content)
    if isinstance(content, list | tuple):
        if content and all(isinstance(item, int | float) for item in content):
            return f"[embedding: {len(content)} 维]"
        if content and all(
            isinstance(item, list | tuple)
            and item
            and all(isinstance(value, int | float) for value in item)
            for item in content
        ):
            return f"[embeddings: {len(content)} 条，维度 {len(content[0])}]"
        return _clip(
            json.dumps(list(content), ensure_ascii=False, indent=2, default=str)
        )
    if isinstance(content, dict):
        return _clip(json.dumps(content, ensure_ascii=False, indent=2, default=str))
    if content is None:
        return ""
    return _clip(str(content))


def _render_usage(usage: Any) -> str:
    if usage is None:
        return "无"
    if isinstance(usage, dict):
        prompt = usage.get("prompt_tokens", usage.get("input_tokens", 0))
        output = usage.get("output_tokens", usage.get("completion_tokens", 0))
        cache = usage.get(
            "cache_hit_tokens",
            usage.get(
                "prompt_cache_hit_tokens", usage.get("cachedContentTokenCount", 0)
            ),
        )
        return f"输入={prompt} 输出={output} 缓存命中={cache}"
    prompt = getattr(usage, "prompt_tokens", None)
    if prompt is not None:
        output = getattr(usage, "output_tokens", 0)
        cache = getattr(usage, "cache_hit_tokens", 0)
        return f"输入={prompt} 输出={output} 缓存命中={cache}"
    return str(usage)


def _format_call(
    *,
    model_name: str,
    model_id: str,
    model_type: str,
    event: str,
    duration: float,
    content: Any,
    response: Any = None,
    usage: Any = None,
    error: BaseException | None = None,
) -> str:
    timestamp = now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    if error is None:
        status = f"Tokens: {_render_usage(usage)}"
    else:
        status = f"失败: {type(error).__name__}: {error}"
    lines = [
        "-" * 100,
        (
            f"{timestamp} | 模型={model_name} | 模型ID={model_id} | "
            f"类型={model_type} | 事件={event} | 耗时={duration:.3f}s | {status}"
        ),
        "--- 输入 ---",
        _render_content(content),
    ]
    if error is None:
        lines.extend(["--- 输出 ---", _render_response(response)])
    else:
        lines.extend(["--- 错误 ---", _clip(str_traceback(error))])
    return _clip("\n".join(lines), MAX_RECORD_CHARS)


def log_llm_call(
    *,
    model: Any,
    model_type: str,
    content: Any,
    event: str,
    duration: float,
    response: Any = None,
    usage: Any = None,
    error: BaseException | None = None,
) -> None:
    """记录一次模型调用；任何异常都不会影响调用方。"""
    if not _initialized:
        init_llm_log()
    if _sink_id is None:
        return
    try:
        _llm_logger.info(
            _format_call(
                model_name=getattr(model, "name", "未知模型"),
                model_id=getattr(model, "model", ""),
                model_type=model_type,
                event=event,
                duration=duration,
                content=content,
                response=response,
                usage=usage,
                error=error,
            )
        )
    except Exception as exc:
        log.warning(f"写入 LLM 日志失败: {exc}")
