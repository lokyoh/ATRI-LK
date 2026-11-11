import asyncio
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from html import escape as _html_escape
from typing import Any

import jieba
from tortoise.expressions import RawSQL
from tortoise.functions import Count, Max, Min

from ATRI.bot.statistics.model import MessageStatistics, ServiceStatistics
from ATRI.utils.datetime import TIMEZONE, now

_TOP_LIMIT = 3
_HOT_WORD_LIMIT = 10
_HOT_SENTENCE_LIMIT = 5
_MIN_WORD_LEN = 2
_MIN_SENTENCE_LEN = 2
_MAX_SENTENCE_LEN = 40
_YEAR_RE = re.compile(r"^(\d{4})$")
_DATE_RE = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$")
_WORD_RE = re.compile(r"^[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9]*$")
_TEXT_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")
_SPACE_RE = re.compile(r"\s+")
_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;，,、：:\n\r]+")
_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
_COMMAND_PREFIXES = ("/", "／", "!", "！", "#", "$", "＄")
_LE_BLOCK_RE = re.compile(r"<le>.*?(?:</le>|$)", re.IGNORECASE | re.DOTALL)
_LE_TAG_RE = re.compile(r"</?le>", re.IGNORECASE)
_MARKDOWN_CARD_RE = re.compile(r"<le>\s*\[markdown:", re.IGNORECASE)
_MD_LINK_RE = re.compile(r"\[[^\]]*]\((?:mqqapi|https?)://[^)]*\)", re.IGNORECASE)
_ESCAPE_RE = re.compile(r"\\(?:[nrt]|u[0-9a-fA-F]{4})")
_ARTIFACT_TAG_RE = re.compile(
    r"\[(?:at|image|reply|markdown|file|json|record|video|face):[^\]]*]",
    re.IGNORECASE,
)
_ARTIFACT_TAIL_RE = re.compile(
    r"\[(?:at|image|reply|markdown|file|json|record|video|face):.*$",
    re.IGNORECASE | re.DOTALL,
)
_EVENT_DESC_RE = re.compile(r"^\d+\s+poke\s+\d+\s+from\s+\d+$", re.IGNORECASE)
_ARTIFACT_FRAGMENT_RE = re.compile(
    r"(?:self_id|user_id|group_id|message_id|post_type|notice_type"
    r"|faceIndex|faceText|faceType|packId)'?\s*:",
    re.IGNORECASE,
)

_STOPWORDS = frozenset(
    """
    的 了 是 我 你 他 她 它 们 在 有 和 就 不 都 也 很 还 这 那 啊 吧 吗 呢
    哦 嗯 呀 嘛 哈 一个 什么 怎么 没有 可以 自己 现在 知道 觉得 因为 所以
    但是 如果 还是 就是 这个 那个 我们 你们 他们 她们 它们 然后 已经 一下
    真的 不是 可能 应该 东西 时候 今天 明天 昨天 晚上 早上 中午 有点 感觉
    好像 需要 喜欢 谢谢 一样 这么 那么 这样 那样 只是 还有 而且 或者 不过
    其实 毕竟 终于 居然 竟然 接着 于是 虽然 要是 除非 除了 关于 对于 为了
    通过 根据 按照 以及 等等 之类 什么的 哪个 这些 那些 哪些 这里 那里 哪里
    怎样 如何 为什么 谁 哪 几 多少 非常 特别 十分 超级 好的 好吧 行 没问题
    收到 了解 明白 true false none null nan
    """.split()  # noqa: SIM905
)


@dataclass(frozen=True)
class SummaryWindow:
    start: datetime
    end: datetime
    includes_last_year: bool


@dataclass(frozen=True)
class TopEntry:
    name: str
    count: int


@dataclass(frozen=True)
class HotTextReport:
    words: list[TopEntry] = field(default_factory=list)
    sentences: list[TopEntry] = field(default_factory=list)


@dataclass(frozen=True)
class ScopeReport:
    """群聊或全局的年度总结。"""

    window: SummaryWindow
    scope_kind: str
    scope_label: str
    message_total: int
    user_total: int
    group_total: int
    service_total: int
    active_days: int
    top_services: list[TopEntry] = field(default_factory=list)
    top_groups: list[TopEntry] = field(default_factory=list)
    top_users: list[TopEntry] = field(default_factory=list)
    busiest_month: TopEntry | None = None
    hot_words: list[TopEntry] = field(default_factory=list)
    hot_sentences: list[TopEntry] = field(default_factory=list)


@dataclass(frozen=True)
class PersonalReport:
    """当前用户自己的年度报告。"""

    window: SummaryWindow
    scope_kind: str
    scope_label: str
    user_id: str
    message_total: int
    share: float
    rank: int | None
    active_days: int
    busiest_month: TopEntry | None = None
    first_message: date | None = None
    last_message: date | None = None
    hot_words: list[TopEntry] = field(default_factory=list)
    hot_sentences: list[TopEntry] = field(default_factory=list)


def parse_target_date(text: str, today: date | None = None) -> date:
    """解析目标日期。

    支持 ``YYYY-MM-DD``、``YYYY/MM/DD``、``YYYY.MM.DD``；只写年份时取该年
    12 月 31 日。留空取今天。
    """
    text = (text or "").strip()
    if not text:
        return today or now().date()

    year_match = _YEAR_RE.fullmatch(text)
    if year_match:
        return date(int(year_match.group(1)), 12, 31)

    date_match = _DATE_RE.fullmatch(text)
    if date_match:
        year, month, day = (int(part) for part in date_match.groups())
        try:
            return date(year, month, day)
        except ValueError as exc:
            raise ValueError("这个日期不存在，检查一下月份和天数吧") from exc

    raise ValueError("日期格式应为 YYYY-MM-DD，或只写年份 YYYY")


def resolve_window(target: date) -> SummaryWindow:
    """按目标日期算出统计窗口。"""
    includes_last_year = target.month <= 3
    start_year = target.year - 1 if includes_last_year else target.year
    start = datetime(start_year, 1, 1, tzinfo=TIMEZONE)
    end = datetime(
        target.year,
        target.month,
        target.day,
        23,
        59,
        59,
        999999,
        tzinfo=TIMEZONE,
    )
    return SummaryWindow(start=start, end=end, includes_last_year=includes_last_year)


def _message_query(bot_id: str, window: SummaryWindow):
    return MessageStatistics.filter(
        bot_id=bot_id,
        created_at__gte=window.start,
        created_at__lte=window.end,
    )


def _service_query(bot_id: str, window: SummaryWindow):
    return ServiceStatistics.filter(
        bot_id=bot_id,
        created_at__gte=window.start,
        created_at__lte=window.end,
    )


async def _distinct_count(query, field: str) -> int:
    rows = await query.annotate(total=Count(field, distinct=True)).values_list(
        "total", flat=True
    )
    return int(rows[0]) if rows else 0


async def _active_days(query) -> int:
    """有消息的自然日数量。"""
    rows = await query.annotate(
        total=Count(RawSQL("DATE(created_at)"), distinct=True)
    ).values_list("total", flat=True)
    return int(rows[0]) if rows else 0


async def _top_entries(query, field: str, limit: int = _TOP_LIMIT) -> list[TopEntry]:
    rows = await (
        query.annotate(total=Count("id"))
        .group_by(field)
        .order_by("-total")
        .limit(limit)
        .values_list(field, "total")
    )
    return [
        TopEntry(name=str(name), count=int(total))
        for name, total in rows
        if name is not None and str(name) != ""
    ]


async def _busiest_month(query) -> TopEntry | None:
    """消息最多的月份。

    ``group_by`` 只接受模型字段名，这里用 ``values`` 配合 RawSQL 别名分组，
    再在 Python 里挑最大值（最多 12 行）。
    """
    rows = await (
        query.annotate(
            month=RawSQL("strftime('%m', created_at)"),
            total=Count("id"),
        )
        .group_by("month")
        .values("month", "total")
    )
    if not rows:
        return None
    # 并列时取更早的月份，避免不同数据库的返回顺序影响结果。
    rows.sort(key=lambda row: row["month"])
    best = max(rows, key=lambda row: row["total"])
    return TopEntry(name=f"{int(best['month'])} 月", count=int(best["total"]))


async def _first_last_dates(query) -> tuple[date | None, date | None]:
    rows = await query.annotate(
        first=Min("created_at"), last=Max("created_at")
    ).values_list("first", "last")
    if not rows:
        return None, None
    first, last = rows[0]
    return (
        first.date() if first else None,
        last.date() if last else None,
    )


def _clean_text(text: str) -> str:
    """去掉 ``<le>`` 富媒体标签、卡片链接等非聊天文本，再压缩空白。"""
    text = _LE_BLOCK_RE.sub(" ", text)
    text = _LE_TAG_RE.sub(" ", text)
    text = _ARTIFACT_TAG_RE.sub(" ", text)
    text = _ARTIFACT_TAIL_RE.sub(" ", text)
    text = _MD_LINK_RE.sub(" ", text)
    text = _ESCAPE_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text).strip()


def _is_card_payload(text: str) -> bool:
    """QQ 机器人发送的 markdown 卡片（运势、宠物等）不是聊天内容。"""
    return bool(_MARKDOWN_CARD_RE.search(text))


def _is_artifact(text: str) -> bool:
    """截断后残留的表情 / 事件字典片段，不参与热词热句。"""
    return bool(_EVENT_DESC_RE.fullmatch(text) or _ARTIFACT_FRAGMENT_RE.search(text))


def _is_command(text: str) -> bool:
    return text.startswith(_COMMAND_PREFIXES)


def _is_valid_word(word: str) -> bool:
    word = word.strip()
    if len(word) < _MIN_WORD_LEN or word.lower() in _STOPWORDS:
        return False
    if not _WORD_RE.fullmatch(word):
        return False
    return not word.isdigit()


def _is_valid_sentence(text: str) -> bool:
    if not (_MIN_SENTENCE_LEN <= len(text) <= _MAX_SENTENCE_LEN):
        return False
    if _is_command(text) or _URL_RE.search(text):
        return False
    return bool(_TEXT_RE.search(text))


def _sentence_candidates(text: str) -> set[str]:
    """一条消息里可以参与热句统计的候选句子。"""
    if _is_command(text) or _URL_RE.search(text):
        return set()

    candidates: set[str] = set()
    normalized = _SPACE_RE.sub(" ", text).strip()
    if _is_valid_sentence(normalized):
        candidates.add(normalized)

    for fragment in _SENTENCE_SPLIT_RE.split(normalized):
        fragment = fragment.strip(" \t，,、:：\"'“”‘’")
        if _is_valid_sentence(fragment):
            candidates.add(fragment)
    return candidates


def analyze_hot_texts(contents: Iterable[str]) -> HotTextReport:
    """从消息文本里统计热词与热句。

    - 先剔除 ``<le>`` 富媒体标签、机器人 markdown 卡片与指令消息；
    - 热词：jieba 分词后过滤停用词、单字和纯数字；
    - 热句：整条短消息或按标点切出的短句，至少出现两次才会上榜。
    """
    word_counter: Counter[str] = Counter()
    sentence_counter: Counter[str] = Counter()

    for content in contents:
        raw = content or ""
        if _is_card_payload(raw):
            continue
        text = _clean_text(raw)
        if not text or _is_command(text) or _is_artifact(text) or _URL_RE.search(text):
            continue

        word_counter.update(
            word.lower() for word in jieba.lcut(text) if _is_valid_word(word)
        )
        sentence_counter.update(_sentence_candidates(text))

    words = [
        TopEntry(name=word, count=count)
        for word, count in sorted(
            word_counter.items(), key=lambda item: (-item[1], item[0])
        )[:_HOT_WORD_LIMIT]
    ]
    sentences = [
        TopEntry(name=sentence, count=count)
        for sentence, count in sorted(
            ((s, c) for s, c in sentence_counter.items() if c >= 2),
            key=lambda item: (-item[1], len(item[0]), item[0]),
        )[:_HOT_SENTENCE_LIMIT]
    ]
    return HotTextReport(words=words, sentences=sentences)


async def _analyze_query_texts(query) -> HotTextReport:
    contents = await query.values_list("content", flat=True)
    return await asyncio.to_thread(analyze_hot_texts, contents)


async def collect_scope_summary(
    *,
    bot_id: str,
    window: SummaryWindow,
    global_scope: bool = False,
    group_id: str | None = None,
    scope_label: str | None = None,
) -> ScopeReport:
    """统计群聊或全局的年度数据。"""
    if not global_scope and not group_id:
        raise ValueError("群聊总结需要提供 group_id")

    message_query = _message_query(bot_id, window)
    service_query = _service_query(bot_id, window)

    if global_scope:
        scope_kind = "global"
        label = scope_label or "全局"
    else:
        scope_kind = "group"
        label = scope_label or f"群 {group_id}"
        message_query = message_query.filter(group_id=group_id)
        service_query = service_query.filter(target_type="group", target_id=group_id)

    message_total = await message_query.count()
    service_total = await service_query.count()
    user_total = await _distinct_count(message_query, "user_id")
    group_total = await _distinct_count(message_query, "group_id")

    top_services = (
        await _top_entries(service_query, "service_id") if service_total else []
    )
    top_users = await _top_entries(message_query, "user_id") if message_total else []
    top_groups = (
        await _top_entries(
            message_query.filter(group_id__not_isnull=True),
            "group_id",
        )
        if message_total and global_scope
        else []
    )
    hot_text = (
        await _analyze_query_texts(message_query) if message_total else HotTextReport()
    )

    return ScopeReport(
        window=window,
        scope_kind=scope_kind,
        scope_label=label,
        message_total=message_total,
        user_total=user_total,
        group_total=group_total,
        service_total=service_total,
        active_days=await _active_days(message_query) if message_total else 0,
        top_services=top_services,
        top_groups=top_groups,
        top_users=top_users,
        busiest_month=await _busiest_month(message_query) if message_total else None,
        hot_words=hot_text.words,
        hot_sentences=hot_text.sentences,
    )


async def collect_personal_summary(
    *,
    bot_id: str,
    window: SummaryWindow,
    user_id: str,
    group_id: str | None = None,
    scope_label: str | None = None,
    scope_total: int | None = None,
) -> PersonalReport:
    """统计当前用户的年度数据。

    传入 ``group_id`` 时只统计该群，否则统计该用户在全部聊天中的表现。
    ``scope_total`` 可复用群/全局总结算好的消息总数，避免重复查询。
    """
    personal_query = _message_query(bot_id, window).filter(user_id=user_id)
    scope_query = _message_query(bot_id, window)

    if group_id:
        scope_kind = "group"
        label = scope_label or f"群 {group_id}"
        personal_query = personal_query.filter(group_id=group_id)
        scope_query = scope_query.filter(group_id=group_id)
    else:
        scope_kind = "all"
        label = scope_label or "全部聊天"

    message_total = await personal_query.count()
    if scope_total is None:
        scope_total = await scope_query.count()
    share = message_total / scope_total if scope_total else 0.0

    rank = None
    if message_total:
        higher = await (
            scope_query.annotate(total=Count("id"))
            .group_by("user_id")
            .filter(total__gt=message_total)
            .values("user_id")
        )
        rank = len(higher) + 1

    first_message, last_message = await _first_last_dates(personal_query)
    hot_text = (
        await _analyze_query_texts(personal_query) if message_total else HotTextReport()
    )

    return PersonalReport(
        window=window,
        scope_kind=scope_kind,
        scope_label=label,
        user_id=user_id,
        message_total=message_total,
        share=share,
        rank=rank,
        active_days=await _active_days(personal_query) if message_total else 0,
        busiest_month=(await _busiest_month(personal_query) if message_total else None),
        first_message=first_message,
        last_message=last_message,
        hot_words=hot_text.words,
        hot_sentences=hot_text.sentences,
    )


def _md_inline(text: str) -> str:
    """转义用户内容，避免破坏 Markdown 结构。"""
    return (
        text.replace("\\", "\\\\")
        .replace("`", "'")
        .replace("*", "\\*")
        .replace("_", "\\_")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("#", "\\#")
        .replace("|", "\\|")
    )


def _format_range_md(window: SummaryWindow) -> str:
    text = f"统计范围：{window.start:%Y-%m-%d} ~ {window.end:%Y-%m-%d}"
    if window.includes_last_year:
        text += "（含去年）"
    return text


def _format_ranked(entries: list[TopEntry], unit: str) -> list[str]:
    return [
        f"{index}. **{_md_inline(entry.name)}** — {entry.count:,} {unit}"
        for index, entry in enumerate(entries, 1)
    ]


def _format_hot_words(entries: list[TopEntry]) -> str:
    return " · ".join(
        f"`{_md_inline(entry.name)}` × {entry.count:,}" for entry in entries
    )


def _format_hot_sentences(entries: list[TopEntry]) -> list[str]:
    return [f"- {_md_inline(entry.name)} × {entry.count:,}" for entry in entries]


def format_scope_report(report: ScopeReport) -> str:
    """把群聊/全局统计排成 Markdown。"""
    year = report.window.end.year
    title = f"# 📊 {year} 年度总结"
    if report.window.includes_last_year:
        title += f"（含 {year - 1} 年）"

    lines = [
        title,
        "",
        f"**{_md_inline(report.scope_label)}**",
        _format_range_md(report.window),
        "",
    ]

    if report.message_total == 0 and report.service_total == 0:
        lines.append("这段时间还没有统计数据呢...")
        return "\n".join(lines)

    lines.extend(["## 📈 数据一览", ""])
    lines.append(f"- 💬 消息总数：**{report.message_total:,}** 条")
    if report.scope_kind == "group":
        average = report.message_total // report.user_total if report.user_total else 0
        lines.append(
            f"- 👥 活跃成员：**{report.user_total:,}** 人 · 人均 **{average:,}** 条"
        )
    else:
        lines.append(
            f"- 👥 活跃用户：**{report.user_total:,}** 人 · "
            f"活跃群聊：**{report.group_total:,}** 个"
        )
    lines.append(f"- 🔧 服务调用：**{report.service_total:,}** 次")
    lines.append(f"- 📅 活跃天数：**{report.active_days:,}** 天")
    if report.busiest_month:
        lines.append(
            f"- 🔥 最热闹：**{_md_inline(report.busiest_month.name)}**"
            f"（{report.busiest_month.count:,} 条）"
        )
    lines.append("")

    if report.top_users:
        user_title = (
            "## 🏆 群活跃榜" if report.scope_kind == "group" else "## 👑 最活跃的用户"
        )
        lines.extend([user_title, ""])
        lines.extend(_format_ranked(report.top_users, "条"))
        lines.append("")
    if report.top_groups:
        lines.extend(["## 🏠 最活跃的群", ""])
        lines.extend(_format_ranked(report.top_groups, "条"))
        lines.append("")
    if report.top_services:
        lines.extend(["## 🔧 最常用服务", ""])
        lines.extend(_format_ranked(report.top_services, "次"))
        lines.append("")
    if report.hot_words:
        lines.extend(["## 🔥 年度热词", "", _format_hot_words(report.hot_words), ""])
    if report.hot_sentences:
        lines.extend(["## 💬 年度热句", ""])
        lines.extend(_format_hot_sentences(report.hot_sentences))
        lines.append("")

    return "\n".join(lines).rstrip()


def _rank_title(rank: int, scope_kind: str) -> str:
    if rank == 1:
        return "群聊之王" if scope_kind == "group" else "全站榜首"
    if rank <= 3:
        return "话题中心"
    if rank <= 10:
        return "活跃分子"
    return "常驻居民"


def format_personal_report(report: PersonalReport) -> str:
    """把个人统计排成 Markdown。"""
    year = report.window.end.year
    title = f"# 👤 你的 {year} 年度报告"
    if report.window.includes_last_year:
        title += f"（含 {year - 1} 年）"

    lines = [
        title,
        "",
        f"**{_md_inline(report.scope_label)}**",
        _format_range_md(report.window),
        "",
    ]

    if report.message_total == 0:
        lines.append("今年还没有发言记录，潜水观察员就是你了 👀")
        return "\n".join(lines)

    scope_name = "全群" if report.scope_kind == "group" else "全部消息"
    lines.extend(["## 📈 你的数据", ""])
    lines.append(
        f"- 💬 你发了 **{report.message_total:,}** 条消息，"
        f"占{scope_name} **{report.share:.1%}**"
    )

    if report.rank is not None:
        rank_name = "群内排名" if report.scope_kind == "group" else "全 bot 排名"
        lines.append(
            f"- 🏅 {rank_name}：**第 {report.rank} 名** · "
            f"{_rank_title(report.rank, report.scope_kind)}"
        )

    lines.append(f"- 📅 活跃天数：**{report.active_days:,}** 天")
    if report.busiest_month:
        lines.append(
            f"- 🔥 最热闹：**{_md_inline(report.busiest_month.name)}**"
            f"（{report.busiest_month.count:,} 条）"
        )
    if report.first_message and report.last_message:
        if report.first_message == report.last_message:
            lines.append(f"- 🕐 发言日期：{report.first_message:%Y-%m-%d}")
        else:
            lines.append(
                f"- 🕐 首次发言：{report.first_message:%Y-%m-%d} · "
                f"最近发言：{report.last_message:%Y-%m-%d}"
            )
    lines.append("")

    if report.hot_words:
        lines.extend(
            ["## 🔥 你的年度热词", "", _format_hot_words(report.hot_words), ""]
        )
    if report.hot_sentences:
        lines.extend(["## 💬 你的口头禅", ""])
        lines.extend(_format_hot_sentences(report.hot_sentences))
        lines.append("")

    return "\n".join(lines).rstrip()


def _range_text(window: SummaryWindow) -> str:
    text = f"{window.start:%Y-%m-%d} ~ {window.end:%Y-%m-%d}"
    if window.includes_last_year:
        text += "（含去年）"
    return text


def _stat(label: str, value: str, extra: str = "") -> dict[str, str]:
    return {"label": label, "value": value, "extra": extra}


def _rank_section(title: str, entries: list[TopEntry], unit: str) -> dict[str, Any]:
    return {
        "title": title,
        "kind": "rank",
        "entries": [
            {"name": _html_escape(entry.name), "value": f"{entry.count:,} {unit}"}
            for entry in entries
        ],
    }


def _word_section(title: str, entries: list[TopEntry]) -> dict[str, Any]:
    return {
        "title": title,
        "kind": "words",
        "entries": [
            {"name": _html_escape(entry.name), "value": f"{entry.count:,}"}
            for entry in entries
        ],
    }


def _sentence_section(title: str, entries: list[TopEntry]) -> dict[str, Any]:
    return {
        "title": title,
        "kind": "sentences",
        "entries": [
            {"name": _html_escape(entry.name), "value": f"{entry.count:,}"}
            for entry in entries
        ],
    }


def scope_report_context(report: ScopeReport) -> dict[str, Any]:
    """把群聊/全局统计转成 ``report.html`` 的模板上下文。"""
    year = report.window.end.year
    title = f"{year} 年度总结"
    if report.window.includes_last_year:
        title += f"（含 {year - 1} 年）"

    context: dict[str, Any] = {
        "title": title,
        "subtitle": f"{_html_escape(report.scope_label)} · {_range_text(report.window)}",
        "badge": "",
        "stats": [],
        "sections": [],
        "empty_text": "",
    }

    if report.message_total == 0 and report.service_total == 0:
        context["empty_text"] = "这段时间还没有统计数据呢..."
        return context

    stats = [_stat("消息总数", f"{report.message_total:,}", "条")]
    if report.scope_kind == "group":
        average = report.message_total // report.user_total if report.user_total else 0
        stats.append(
            _stat("活跃成员", f"{report.user_total:,}", f"人 · 人均 {average:,} 条")
        )
    else:
        stats.append(
            _stat(
                "活跃用户",
                f"{report.user_total:,}",
                f"人 · 活跃群聊 {report.group_total:,} 个",
            )
        )
    stats.append(_stat("服务调用", f"{report.service_total:,}", "次"))
    stats.append(_stat("活跃天数", f"{report.active_days:,}", "天"))
    if report.busiest_month:
        stats.append(
            _stat(
                "最热闹",
                _html_escape(report.busiest_month.name),
                f"{report.busiest_month.count:,} 条",
            )
        )
    context["stats"] = stats

    sections: list[dict[str, Any]] = []
    if report.top_users:
        user_title = "群活跃榜" if report.scope_kind == "group" else "最活跃的用户"
        sections.append(_rank_section(user_title, report.top_users, "条"))
    if report.top_groups:
        sections.append(_rank_section("最活跃的群", report.top_groups, "条"))
    if report.top_services:
        sections.append(_rank_section("最常用服务", report.top_services, "次"))
    if report.hot_words:
        sections.append(_word_section("年度热词", report.hot_words))
    if report.hot_sentences:
        sections.append(_sentence_section("年度热句", report.hot_sentences))
    context["sections"] = sections

    return context


def personal_report_context(report: PersonalReport) -> dict[str, Any]:
    """把个人统计转成 ``report.html`` 的模板上下文。"""
    year = report.window.end.year
    title = f"你的 {year} 年度报告"
    if report.window.includes_last_year:
        title += f"（含 {year - 1} 年）"

    badge = ""
    if report.rank is not None:
        badge = f"第 {report.rank} 名 · {_rank_title(report.rank, report.scope_kind)}"

    context: dict[str, Any] = {
        "title": title,
        "subtitle": f"{_html_escape(report.scope_label)} · {_range_text(report.window)}",
        "badge": badge,
        "stats": [],
        "sections": [],
        "empty_text": "",
    }

    if report.message_total == 0:
        context["empty_text"] = "今年还没有发言记录，潜水观察员就是你了"
        return context

    scope_name = "全群" if report.scope_kind == "group" else "全部消息"
    stats = [
        _stat(
            "消息总数",
            f"{report.message_total:,}",
            f"条 · 占{scope_name} {report.share:.1%}",
        ),
        _stat("活跃天数", f"{report.active_days:,}", "天"),
    ]
    if report.busiest_month:
        stats.append(
            _stat(
                "最热闹",
                _html_escape(report.busiest_month.name),
                f"{report.busiest_month.count:,} 条",
            )
        )
    if report.first_message and report.last_message:
        if report.first_message == report.last_message:
            stats.append(_stat("发言日期", f"{report.first_message:%Y-%m-%d}"))
        else:
            stats.append(_stat("首次发言", f"{report.first_message:%Y-%m-%d}"))
            stats.append(_stat("最近发言", f"{report.last_message:%Y-%m-%d}"))
    context["stats"] = stats

    sections: list[dict[str, Any]] = []
    if report.hot_words:
        sections.append(_word_section("你的年度热词", report.hot_words))
    if report.hot_sentences:
        sections.append(_sentence_section("你的口头禅", report.hot_sentences))
    context["sections"] = sections

    return context
