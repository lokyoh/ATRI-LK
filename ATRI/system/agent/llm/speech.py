from __future__ import annotations

import re
import unicodedata

#: 截断时优先落在这类字符之后，让语音停在一句话的结尾。
_SENTENCE_ENDS = ("。", "！", "？", "…", "；", ".", "!", "?", ";", "\n")

_CQ_RE = re.compile(r"\[CQ:[^\]]*\]")
_TAG_RE = re.compile(r"<[^>\n]{1,60}>")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
#: 括号动作描写：与 reply_pipeline 的清洗口径一致，只吃短括号。
_ACTION_RE = re.compile(r"[（(][^（）()]{0,12}[）)]")
#: 星号动作描写：`*点头*`；两边各两个星的强调写作 `**强调**`，这里先把它拆成
#: 纯文本，不能当作动作描写删掉——那会把要说的话一起吞掉。
_STAR_ACTION_RE = re.compile(r"(?<!\*)\*[^*\n]{1,20}\*(?!\*)")
_MARK_RE = re.compile(r"[*_`#>~|]+")


def _speakable(char: str) -> bool:
    """保留文字、数字、标点与空白；丢弃表情等装饰性符号。"""
    if char.isspace():
        return True
    category = unicodedata.category(char)
    return category[0] in {"L", "N", "P"}


def prepare_speech_text(text: str, max_chars: int | None = None) -> str:
    """清洗并（可选）按句号截断；没有可读内容时返回空串。"""
    body = str(text or "").strip()
    if not body:
        return ""

    body = _CQ_RE.sub("", body)
    body = _MD_LINK_RE.sub(r"\1", body)
    body = _TAG_RE.sub("", body)
    body = _URL_RE.sub("", body)
    body = _ACTION_RE.sub("", body)
    body = _STAR_ACTION_RE.sub("", body)
    body = _MARK_RE.sub("", body)
    body = "".join(char for char in body if _speakable(char))
    # 换行与连续空白在语音里没有意义，压成一个停顿。
    body = re.sub(r"\s+", " ", body).strip()

    if max_chars is not None and max_chars > 0 and len(body) > max_chars:
        head = body[:max_chars]
        cut = -1
        for index in range(len(head) - 1, len(head) // 2, -1):
            if head[index] in _SENTENCE_ENDS:
                cut = index + 1
                break
        body = (head[:cut] if cut > 0 else head).strip()
    return body


__all__ = ["prepare_speech_text"]
