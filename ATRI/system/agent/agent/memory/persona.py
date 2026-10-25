from __future__ import annotations

import random
import re
import time
from typing import Any

#: 记忆条目里形如 `2025年10月25日Sat-14时57分 ` 的时间前缀
_TS_PREFIX = re.compile(
    r"^\d{4}年\d{1,2}月\d{1,2}日\s*\w{0,3}\s*-?\s*\d{1,2}时\d{1,2}分\s*"
)

#: 时间质感的分档：(秒, 措辞)。刻意模糊——人不会记得精确时间戳。
_AGE_BUCKETS = (
    (60 * 60 * 6, "刚刚"),
    (60 * 60 * 24, "今天早些时候"),
    (60 * 60 * 24 * 2, "昨天"),
    (60 * 60 * 24 * 5, "前几天"),
    (60 * 60 * 24 * 20, "前阵子"),
    (60 * 60 * 24 * 60, "上个月"),
    (60 * 60 * 24 * 300, "几个月前"),
    (60 * 60 * 24 * 730, "去年"),
)


def strip_timestamp(text: str) -> str:
    """去掉记忆自带的日期前缀。

    前缀是写入时为了排序加的，对读者是噪声；时间感由 `time_text` 用
    相对措辞重建，比绝对时间戳更像回忆。
    """
    body = str(text or "").strip()
    return _TS_PREFIX.sub("", body, count=1).strip() or body


def time_text(created_at: float | None, now: float | None = None) -> str:
    """把绝对时间戳换成人类的时间质感。"""
    if not created_at:
        return ""
    now = time.time() if now is None else now
    delta = max(0.0, now - float(created_at))
    for limit, label in _AGE_BUCKETS:
        if delta < limit:
            return label
    return "很久以前"


def _as_items(memories: Any) -> list[str]:
    """接受 str / list[str] / list[dict]，统一成纯文本条目。"""
    return [text for text, _ in _as_entries(memories)]


def _as_entries(memories: Any) -> list[tuple[str, float]]:
    """取出 (文本, 情绪强度) 对。

    情绪值在 dict 里可能位于 ``emotion`` 字段，也可能在 ``metadata.emotion``
    （数据库行是后者）。两种都接受，缺失按 0 处理。
    """
    if not memories:
        return []
    if isinstance(memories, str):
        return [(memories, 0.0)] if memories.strip() else []
    entries: list[tuple[str, float]] = []
    for item in memories:
        emotion = 0.0
        if isinstance(item, dict):
            text = str(item.get("content") or "").strip()
            raw = item.get("emotion")
            if raw is None:
                raw = (item.get("metadata") or {}).get("emotion")
            try:
                emotion = abs(float(raw or 0.0))
            except (TypeError, ValueError):
                emotion = 0.0
        else:
            text = str(item or "").strip()
        if text:
            entries.append((text, min(1.0, emotion)))
    return entries


def pick_weighted(
    entries: list[tuple[str, float]],
    limit: int,
    *,
    rng: random.Random | None = None,
) -> list[str]:
    """按情绪强度加权挑选，返回**由新到旧**的文本列表。

    三条设计约束：

    1. **不是纯随机**。纯随机会让她「记得」一件完全无关的旧事，显得精神错乱。
       权重 = ``1 + 3*|emotion|``，高情绪记忆被选中的机会更大，但平淡的也可能出现
       ——人的记忆本来就有这种噪声。
    2. **保留最近几条的确定性**。最近的 2 条一定入选，因为那是「刚发生的事」，
       她不该忘。剩下的名额才做加权抽样。
    3. **结果按原顺序返回**（由旧到新的书写顺序会被调用方反转），
       避免抽出来的记忆在时间上乱跳。
    """
    if limit <= 0 or not entries:
        return []
    if len(entries) <= limit:
        return [text for text, _ in entries]

    rng = rng or random
    anchor = min(2, limit)
    guaranteed_idx = list(range(len(entries) - anchor, len(entries)))
    remaining_idx = [i for i in range(len(entries)) if i not in guaranteed_idx]

    pool = list(remaining_idx)
    weights = [1.0 + 3.0 * entries[i][1] for i in pool]
    chosen: list[int] = []
    need = limit - len(guaranteed_idx)
    while pool and len(chosen) < need:
        pick = rng.choices(range(len(pool)), weights=weights, k=1)[0]
        chosen.append(pool.pop(pick))
        weights.pop(pick)

    final = sorted(guaranteed_idx + chosen)
    return [entries[i][0] for i in final]


def render_memories(
    user_name: str,
    memories: Any,
    *,
    limit: int = 11,
    budget: int = 500,
) -> str:
    """渲染成第一人称的回忆段落；没有可用内容时返回空串。

    P3：选条策略从「取最近 N 条」改成**情绪加权**。人不会只记得最近的，
    而是会突然想起很久以前的一件小事——那件事通常情绪很强烈。
    所以这里按 ``(1 + 3*|emotion|)`` 加权，高情绪的记忆即使很旧也有机会被选中。

    :param limit: 最多渲染多少条（`USER_MEMORY_LIMIT` 量级）。
    :param budget: 字数预算，超出后停止追加并显式说明还有多少条。
    """
    entries = _as_entries(memories)
    if not entries:
        return ""

    picked = pick_weighted(entries, limit)
    lines: list[str] = []
    used = 0
    for text in picked:
        body = strip_timestamp(text)
        if not body:
            continue
        if used + len(body) > budget:
            break
        lines.append(body)
        used += len(body)
    if not lines:
        return ""

    remaining = len(entries) - len(lines)
    head = f"关于{user_name}，你记得这些事（用第一人称自然地想起，不要逐条复述）："
    body = "\n".join(f"- {line}" for line in lines)
    tail = (
        f"\n（还有 {remaining} 件更早的事，需要时可以再回想）" if remaining > 0 else ""
    )
    return f"{head}\n{body}{tail}"


def render_self_memories(memories: Any, *, limit: int = 6, budget: int = 300) -> str:
    """渲染「亚托莉自己的经历」——这是她有连续人格的来源。

    与用户记忆分开渲染：自己的经历可以跨群复用，不涉及他人隐私，
    但在群里全部倒出来会显得自恋，所以默认条数更少。

    自我记忆目前是纯文本列表（没有情绪字段），所以这里不做加权抽样，
    保持「最近的优先」——她自己刚经历的事最该延续。
    """
    items = _as_items(memories)
    if not items:
        return ""
    picked = items[-limit:] if len(items) > limit else items
    lines: list[str] = []
    used = 0
    for raw in reversed(picked):
        body = strip_timestamp(raw)
        if not body or used + len(body) > budget:
            continue
        lines.append(body)
        used += len(body)
    if not lines:
        return ""
    body = "\n".join(f"- {line}" for line in lines)
    return f"你自己身上发生过这些事（可自然地流露，不必刻意强调）：\n{body}"
