from __future__ import annotations

import difflib
import json
import re
import time
from collections import deque

#: 单条文本上限的兜底值；运行时以 `AgentConfig.reply_max_chars` 为准。
REPLY_MAX_CHARS = 300

#: 一次 `say` 最多分几条
MAX_MESSAGES = 3

#: markdown 标题 / 正文上限
MARKDOWN_TITLE_CHARS = 40
MARKDOWN_BODY_CHARS = 2000

#: 表情附带文本上限
FACE_TEXT_CHARS = 40

_SENTENCE_ENDS = ("。", "！", "？", "…", "\n", "；", ".", "!", "?", "；")

_FENCE_FULL_RE = re.compile(r"^```[^\n]*\n(.*?)\n?```$", re.DOTALL)
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
_RECENT_SENT: dict[str, deque[tuple[float, str]]] = {}
_RECENT_LIMIT = 8

#: 去重窗口（秒）：只挡同一轮里被拆散/复读的句子，跨回合不算重复。
_RECENT_WINDOW = 60.0

#: 近似重复判定：去掉标点与语气词后，两句话的相似度阈值。
#: 0.5 会把「他不喜欢」和「他喜欢」判成同一句。
_SIMILAR_RATIO = 0.85
#: 太短的话不算近似重复——「好」「嗯」本来就该保留短句自由。
_SIMILAR_MIN_CHARS = 8

_PUNCT_RE = re.compile(
    r"[!！?？。.，,、；;：:…~～\-—\s　\"'“”‘’()（）【】\[\]<>《》*#`]"
)
_TONE_RE = re.compile(r"[哈啊呀嘛啦呢欸诶哎唉哦喔哟咯]")
#: 否定/数字/时间词：任一边有差异就不算重复（反义句不能被吞）。
_DIFF_RE = re.compile(
    r"不|没|别|勿|莫|[0-9零一二三四五六七八九十百千万两半几多]|"
    r"昨天|今天|明天|前天|后天|上午|下午|早上|晚上|小时|分钟|天|周|月|年"
)


def _normalize(text: str) -> str:
    """去标点与语气词，只留骨架：换词重说同一句时骨架几乎一样。"""
    return _TONE_RE.sub("", _PUNCT_RE.sub("", str(text or "")))


def _differs(a: str, b: str) -> bool:
    """两句在否定词/数字/时间词上有差异，就不是复读。"""
    return _DIFF_RE.findall(a) != _DIFF_RE.findall(b)


def _is_similar(a: str, b: str) -> bool:
    na, nb = _normalize(a), _normalize(b)
    if min(len(na), len(nb)) < _SIMILAR_MIN_CHARS:
        return False
    if _differs(na, nb):
        return False
    return difflib.SequenceMatcher(None, na, nb).ratio() >= _SIMILAR_RATIO


def _recent_for(chat_id: str, now: float) -> deque[tuple[float, str]]:
    recent = _RECENT_SENT.setdefault(chat_id, deque(maxlen=_RECENT_LIMIT))
    while recent and now - recent[0][0] > _RECENT_WINDOW:
        recent.popleft()
    return recent


def dedupe(messages: list[str], chat_id: str = "") -> list[str]:
    """丢掉重复消息：完全相同的直接丢弃，换词复读的按骨架相似度丢弃。

    只在一个短窗口内生效：跨回合的「同一句话」是正常表达，
    定时提醒第二天再发一遍不该被自己挡掉。
    """
    now = time.monotonic()
    recent = _recent_for(chat_id, now)
    kept: list[str] = []
    for message in messages:
        body = str(message or "").strip()
        if not body:
            continue
        if any(body == other for _, other in recent):
            continue
        if kept and body == kept[-1]:
            continue
        if any(_is_similar(body, other) for _, other in recent):
            continue
        kept.append(body)
        recent.append((now, body))
    return kept


def _strip_fences(raw: str) -> str:
    """只有整段被围栏包住才剥壳。

    用 ``search`` 会命中正文里的第一个代码块——卡片正文本来就允许代码块，
    那样剥出来只剩 ``pip install`` 一行，整段 JSON 就没了。
    """
    body = str(raw or "").strip()
    match = _FENCE_FULL_RE.match(body)
    return match.group(1).strip() if match else body


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


def parse_markdown(raw: str, fallback: str) -> dict:
    """解析卡片输出，返回 ``{title, body}``。

    正文按 Markdown 原样保留：标题、列表、代码块都是卡片要表达的东西，
    走 :func:`sanitize` 会把它们全拆掉。只删图片链接——图片只能由调用方
    显式给出（现在已经没有这个入口了），模型不许自己在正文里插。
    """
    data = _load_json(raw)
    title = str((data or {}).get("title") or "").strip()
    body = str((data or {}).get("body") or "").strip()
    if not body:
        body = fallback.strip()
    body = _IMAGE_LINK_RE.sub("", body)
    body = _URL_IMAGE_RE.sub("", body)
    body = _truncate_markdown(body, MARKDOWN_BODY_CHARS)
    return {"title": title[:MARKDOWN_TITLE_CHARS], "body": body}


def _truncate_markdown(body: str, limit: int) -> str:
    """按长度截断，但不把代码围栏切成两半。

    截断点若落在未闭合的代码块里，补一行 ``` 收尾——否则渲染出的卡片
    会把后面所有内容都当成代码。
    """
    body = body.strip()
    if len(body) <= limit:
        return body
    head = body[:limit]
    if head.count("```") % 2:
        head += "\n```"
    return head.strip()


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
