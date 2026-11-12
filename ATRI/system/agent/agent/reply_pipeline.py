"""聊天输出的解析、清洗、分条与去重（计划 3.5.4）。

这一层不碰模型、不碰发送，只负责把 Chat 模型的原始输出变成「可以发出去
的最终内容」。所有函数都必须**容错**：模型偶尔会带代码围栏、JSON 前后多
一句话、或把多个回复塞进一段，这些都不应该导致整条消息丢失。
"""

from __future__ import annotations

import json
import re
from collections import deque

#: 单条文本上限；与 `function_calling.REPLY_MAX_CHARS` 保持一致
REPLY_MAX_CHARS = 300

#: 一次 `say` 最多分几条
MAX_MESSAGES = 3

#: markdown 标题 / 正文上限
MARKDOWN_TITLE_CHARS = 40
MARKDOWN_BODY_CHARS = 800

#: 表情附带文本上限
FACE_TEXT_CHARS = 40

_SENTENCE_ENDS = ("。", "！", "？", "…", "\n", "；", ".", "!", "?", "；")

_FENCE_RE = re.compile(r"```[^\n]*\n?(.*?)```", re.DOTALL)
_SPEAKER_PREFIX_RE = re.compile(
    r"^\s*(?:ATRI|亚托莉|机器人|回复|答|A)\s*[:：]\s*", re.IGNORECASE
)
_ACTION_CJK_RE = re.compile(r"[（(][^（）()]{0,8}[）)]")
_ACTION_STAR_RE = re.compile(r"\*[^*\n]{1,20}\*")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s*", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_IMAGE_LINK_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_URL_IMAGE_RE = re.compile(r"https?://\S+\.(?:png|jpg|jpeg|gif|webp)\b", re.IGNORECASE)

#: 最近已发送内容，用于跨条去重；按 chat_id 分别记录。
_RECENT_SENT: dict[str, deque[str]] = {}
_RECENT_LIMIT = 5


def _strip_fences(raw: str) -> str:
    body = str(raw or "")
    match = _FENCE_RE.search(body)
    if match:
        return match.group(1).strip()
    return body.strip()


def _load_json(raw: str) -> dict | None:
    body = _strip_fences(raw)
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        cleaned = re.sub(r",\s*([}\]])", r"\1", body)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def sanitize(text: str) -> str:
    """去掉说话人前缀、动作描写、Markdown 记号与首尾引号。"""
    body = str(text or "").strip()
    if not body:
        return ""
    body = _strip_fences(body)
    body = _SPEAKER_PREFIX_RE.sub("", body)
    body = _ACTION_CJK_RE.sub("", body)
    body = _ACTION_STAR_RE.sub("", body)
    body = _HEADING_RE.sub("", body)
    body = _BULLET_RE.sub("", body)
    body = body.replace("`", "")
    body = body.strip().strip('"“”「」『』').strip()
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


def _reply_max_chars() -> int:
    """运行时读取单条上限，让 WebUI 配置立即生效。"""
    try:
        from ..config import config

        return max(50, min(1000, int(config.get("reply_max_chars") or REPLY_MAX_CHARS)))
    except Exception:
        return REPLY_MAX_CHARS


def split(text: str, max_chars: int | None = None) -> list[str]:
    """把过长文本按句子边界切开，每段不超过 max_chars。"""
    limit = _reply_max_chars() if max_chars is None else int(max_chars)
    body = str(text or "").strip()
    if not body:
        return []
    if len(body) <= limit:
        return [body]

    parts: list[str] = []
    rest = body
    while rest:
        if len(rest) <= limit:
            parts.append(rest.strip())
            break
        head = rest[:limit]
        cut = -1
        for index in range(len(head) - 1, len(head) // 2, -1):
            if head[index] in _SENTENCE_ENDS:
                cut = index + 1
                break
        if cut <= 0:
            cut = limit
        parts.append(rest[:cut].strip())
        rest = rest[cut:].lstrip()
    return [part for part in parts if part]


def dedupe(messages: list[str], chat_id: str = "") -> list[str]:
    """丢掉与最近发送内容完全相同的消息；同时更新最近记录。"""
    recent = _RECENT_SENT.setdefault(chat_id, deque(maxlen=_RECENT_LIMIT))
    kept: list[str] = []
    for message in messages:
        body = str(message or "").strip()
        if not body:
            continue
        if body in recent:
            continue
        if kept and body == kept[-1]:
            continue
        kept.append(body)
        recent.append(body)
    return kept


#: 非 JSON 输出里常见的「助手口吻」开头：这些不是要说的话，是它在解释自己要做什么。
_META_PREFIXES = (
    "好的",
    "好，",
    "明白",
    "收到",
    "我这就",
    "我来",
    "以下是",
    "下面",
    "这是",
    "根据",
    "按照",
    "作为",
    "抱歉",
    "对不起",
    "我无法",
    "我不能",
    "无法",
)
_META_MARKERS = ("json", "JSON", "输出格式", "参数", "messages", "意图")


def _looks_like_meta(text: str) -> bool:
    """这段文本像「解释/拒绝/格式说明」而不是要发出去的话吗？"""
    body = str(text or "").strip()
    if not body:
        return True
    if body.startswith("{") or body.startswith("["):
        return True
    if any(body.startswith(prefix) for prefix in _META_PREFIXES):
        return True
    return any(marker in body for marker in _META_MARKERS)


def parse_messages(raw: str, fallback: str, count: int = 1) -> list[str]:
    """解析 Chat 模型的 text 输出，返回清洗后的 1-3 条。

    Chat 模型偶尔不输出 JSON 而是直接写一句话。那句话可能是要说的话，也可能
    是「好的，我来表达…」「抱歉，我无法…」这类解释。后者发出去就是事故，
    所以非 JSON 路径要过一遍 :func:`_looks_like_meta`：像解释就丢弃，
    退回 intent 兜底。
    """
    limit = max(1, min(MAX_MESSAGES, int(count or 1)))
    fallback_body = sanitize(fallback)
    messages: list[str] = []

    data = _load_json(raw)
    if data is not None and isinstance(data.get("messages"), list):
        messages = [str(item) for item in data["messages"] if str(item).strip()]
    if not messages:
        body = _strip_fences(raw)
        if "\n---\n" in body:
            candidates = [item for item in body.split("\n---\n") if item.strip()]
        elif body.strip():
            candidates = [body]
        else:
            candidates = []
        # 只有「不像解释」的那条才当正文；全像解释就交给 intent 兜底。
        messages = [item for item in candidates if not _looks_like_meta(item)]

    cleaned: list[str] = [sanitize(item) for item in messages]
    cleaned = [item for item in cleaned if item]
    if len(cleaned) > limit:
        # 超过条数时把余下内容并回最后一条，避免直接丢字
        cleaned = [
            *cleaned[: limit - 1],
            "".join(cleaned[limit - 1 :]),
        ]
    final: list[str] = []
    for item in cleaned:
        final.extend(split(item))
    if len(final) > limit:
        final = [*final[: limit - 1], "".join(final[limit - 1 :])]
        final = split(final[-1])
    final = [item for item in final if item][:limit]
    if not final and fallback_body:
        final = split(fallback_body)[:limit]
    return final


def parse_markdown(raw: str, fallback: str, image: str = "") -> dict:
    """解析卡片输出，返回 ``{title, body, image}``；图片只认传入的那张。"""
    data = _load_json(raw)
    title = str((data or {}).get("title") or "").strip()
    body = str((data or {}).get("body") or "").strip()
    if not body:
        body = sanitize(fallback)
    body = _IMAGE_LINK_RE.sub("", body)
    body = _URL_IMAGE_RE.sub("", body)
    body = sanitize(body) if body.startswith(("#", "-", "*")) else body.strip()
    title = sanitize(title)[:MARKDOWN_TITLE_CHARS]
    body = body[:MARKDOWN_BODY_CHARS].strip()
    return {"title": title, "body": body, "image": str(image or "").strip()}


def parse_face(raw: str, available: list[dict]) -> dict:
    """解析表情输出；id 不在可用列表时返回空表情。"""
    ids = {str(item.get("id") or "") for item in available}
    data = _load_json(raw)
    face = str((data or {}).get("face") or "").strip()
    text = sanitize(str((data or {}).get("text") or ""))[:FACE_TEXT_CHARS]
    if face not in ids:
        face = ""
    return {"id": face, "text": text}


def reset_recent(chat_id: str = "") -> None:
    """清空去重记录（测试用）。"""
    if chat_id:
        _RECENT_SENT.pop(chat_id, None)
    else:
        _RECENT_SENT.clear()
