import datetime
import os
import re
from typing import Any

from ATRI.log import log
from ATRI.utils.datetime import today

from ...llm import ModelRequestError, llm_manager
from ..history import history_logger
from ..telemetry import telemetry
from .database import (
    EMBEDDING_MODEL_KEY,
    EXPIRED_GRACE_DAYS,
    SELF_SUBJECT,
    VALID_UNTIL_KEY,
    MemoryDB,
    public_view,
)

#: 自动回忆注入的条数与最低相关度。宁少勿滥：3 条足够勾起联想，
#: 再多就变成把记忆表摊开给模型看。
PREFETCH_LIMIT = 3
PREFETCH_MIN_SIMILARITY = 0.3

MEMORY_BATCH_SIZE = 105
MEMORY_STEP = 100
MEMORY_TAIL_SIZE = 120
MAX_MEMORIES_PER_BATCH = 12
DEFAULT_MEMORY_IMPORTANCE = 0.5
LAST_SUMMARY_TIME_FILE = "last_summary_time"
HISTORY_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")


MEMORY_PROMPT = """你是聊天记录记忆提取器。请从下面这一段群聊记录中提取值得长期记住的事实。

严格要求：
1. 只输出记忆短句，每行一条，不要标题、解释、序号或 Markdown。
2. 宁缺毋滥：整段记录可以不输出任何记忆；最多输出 12 条最重要的记忆。
3. 提取标准是**这件事对她有没有触动**，而不是「信息是否有用」：
   - 该记：让她有情绪反应的事（开心、被冒犯、被需要、尴尬、意外）、
     稳定的个人偏好、身份信息、重要关系、长期计划、明确决定。
   - 不必记：她听了没感觉的普通信息，哪怕它「以后可能用得上」。
   「用户是程序员」这种标签如果没有情绪色彩，不必记成记忆。
4. 不要提取问候、寒暄、情绪宣泄、临时状态、普通问答、单次闲聊或重复内容。
5. 每条必须是独立、简短的中文句子，可以带一点她的主观色彩（如「说的时候很认真」）。
6. 为每条记忆评估三个数值：
   - **重要度**（0.0 到 1.0）：这条值不值得长期留着。宁低勿高。
   - **情绪**（-1.0 到 1.0）：这件事对她的情绪冲击有多强，负数是负面情绪。
     被夸、被逗笑接近 1.0；被冒犯、被冷落接近 -1.0；纯客观信息接近 0。
     情绪越强，这条记忆会被记得越久、越容易被突然想起。
   - **时效**：这条事实什么时候就不成立了。只有「明天」「下周」「寒假期间」
     「截止到某日」这类**带期限**的事实才需要写，格式 [时效:YYYY-MM-DD]；
     稳定的偏好、身份、关系一律**不要写**这一项，省略即永久有效。
     宁可不写也不要猜：猜错的时效会让一条正确的记忆提前失效。
7. 每条记忆必须原样包含数值、人物标记和聊天ID标记，格式必须是
    [重要度:0.0到1.0] [情绪:-1.0到1.0] [时效:YYYY-MM-DD（可选）]
    [人物ID:人物id] [聊天ID:聊天id] 记忆内容
    今天的日期是 {today}，据此推算时效的绝对日期。
8. 人物 ID 必须来自消息前的 sender；聊天 ID 使用本段记录的 chat_id
9. 不要猜测记录中没有出现的 ID，不要把多个人物或多个事实合并成一条。
10. 如果没有符合条件的内容，只输出：无

本段群 ID：{chat_id}
聊天记录：
{history}
"""

#: 记忆条目里的人物标记，``[人物ID:xxx]``。
_PERSON_TAG = re.compile(r"\[人物ID\s*:\s*([^\]]+)\]")

#: 匹配 `[情绪:-0.7]`，允许负号与缺省小数位
_EMOTION_RE = re.compile(r"\[情绪\s*:\s*(-?\d+(?:\.\d+)?)\]")


#: 匹配 `[重要度:0.5]`，与解析条目用的正则保持一致。
_IMPORTANCE_RE = re.compile(r"\[重要度\s*:\s*(0(?:\.\d+)?|1(?:\.0+)?)\]")

#: 可选的时效标记 `[时效:2026-11-20]`。缺省即永久有效。
_VALIDITY_RE = re.compile(r"\[时效\s*:\s*(\d{4}-\d{2}-\d{2})\]")


def _parse_memory_line(line: str) -> tuple[float, float, str, float]:
    """解析一行记忆，返回 (重要度, 情绪, 正文, 失效时间戳)。

    正文为空即格式不对，调用方跳过。失效时间戳为 0 表示永久有效。
    """
    body = (line or "").strip()
    match = _IMPORTANCE_RE.search(body)
    if not match:
        return 0.0, 0.0, "", 0.0
    importance = float(match.group(1))
    emotion = 0.0
    emotion_match = _EMOTION_RE.search(body)
    if emotion_match:
        try:
            emotion = max(-1.0, min(1.0, float(emotion_match.group(1))))
        except ValueError:
            emotion = 0.0
        body = _EMOTION_RE.sub("", body, count=1).strip()
    valid_until = 0.0
    validity_match = _VALIDITY_RE.search(body)
    if validity_match:
        valid_until = _expiry_stamp(validity_match.group(1))
        body = _VALIDITY_RE.sub("", body, count=1).strip()
    body = _IMPORTANCE_RE.sub("", body, count=1).strip()
    return importance, emotion, body, valid_until


def _expiry_stamp(day: str) -> float:
    """把 `YYYY-MM-DD` 换成「那天的最后一刻」，过期判断才不会早关一天。"""
    try:
        parsed = datetime.date.fromisoformat(day)
    except (TypeError, ValueError):
        return 0.0
    end = datetime.datetime.combine(parsed, datetime.time(23, 59, 59))
    return end.timestamp()


def _today_stamp() -> str:
    return datetime.date.today().isoformat()


def _subject_of(memory: str) -> str:
    """条目里的人物 ID 就是这条记忆的「关于谁」；bot 自己的话归 self。"""
    match = _PERSON_TAG.search(memory or "")
    if not match:
        return ""
    who = match.group(1).strip()
    return SELF_SUBJECT if who == "bot" else who


def _has_embedding_model() -> bool:
    return llm_manager.has_slot("embedding")


class MemoryManager:
    def __init__(self):
        self.database = MemoryDB()

    async def get_memories(
        self,
        query: str,
        top_k: int,
        min_similarity: float | None = None,
        chat_id: str | None = None,
        subject: str | None = None,
    ) -> list[dict]:
        """检索长期记忆。没有向量模型时退回关键词检索，而不是查不到。"""
        query = query.strip()
        if not query:
            telemetry.memory(queried=False, hits=0, degraded=True)
            return []
        if not _has_embedding_model():
            return self._lexical_memories(
                query, top_k, chat_id, subject, degraded=False
            )
        try:
            vectors = (
                await llm_manager.call_agent(
                    "embedding", query, "查询记忆", app_id="memoryquery"
                )
            ).content
            vector = vectors[0]
        except (
            IndexError,
            KeyError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as exc:
            # 向量服务抖动时同样退回关键词：宁可查得糙，也别等于没有记忆。
            log.warning(f"记忆查询 embedding 生成失败，退回关键词检索：{exc}")
            return self._lexical_memories(query, top_k, chat_id, subject, degraded=True)
        _, model_id = llm_manager.embedding_identity()
        rows = self.database.query_by_vector(
            vector,
            top_k=top_k,
            min_similarity=min_similarity,
            chat_id=chat_id,
            subject=subject,
            embedding_model=model_id or None,
        )
        telemetry.memory(
            queried=True,
            hits=len(rows),
            top_similarity=(rows[0].get("similarity") if rows else None),
        )
        return [public_view(m) for m in rows]

    def _lexical_memories(
        self,
        query: str,
        top_k: int,
        chat_id: str | None,
        subject: str | None,
        *,
        degraded: bool,
    ) -> list[dict]:
        """没有可用向量时的关键词兜底，口径与向量检索一致（仍做会话/主体隔离）。"""
        rows = self.database.query_by_lexical(
            query,
            top_k=int(top_k),
            chat_id=chat_id,
            subject=subject,
        )
        telemetry.memory(
            queried=bool(rows),
            hits=len(rows),
            top_similarity=(rows[0].get("similarity") if rows else None),
            degraded=degraded,
        )
        return [public_view(m) for m in rows]

    async def recall_for_turn(
        self,
        query: str,
        chat_id: str | None,
        subject: str | None,
        *,
        limit: int = PREFETCH_LIMIT,
        min_similarity: float = PREFETCH_MIN_SIMILARITY,
    ) -> list[dict]:
        """按当前消息预取少量相关记忆。

        自动回忆不该拖累主链路：任何异常都静默降级为空列表，
        她少想起一件事，总好过这一轮直接失败。
        """
        text = (query or "").strip()
        if not text:
            return []
        try:
            return await self.get_memories(
                text,
                limit,
                min_similarity,
                chat_id=chat_id,
                subject=subject,
            )
        except Exception as exc:
            log.debug(f"预取记忆失败，本轮不注入：{exc}")
            return []

    async def get_memories_by_date(self, date: str) -> list[dict[str, Any]]:
        try:
            parsed_date = datetime.date.fromisoformat(date)
        except (TypeError, ValueError):
            return []
        if parsed_date.isoformat() != date:
            return []
        return self.database.memories_by_date(date)

    async def access_memory(
        self, memory: dict[str, Any], reinforce: float = 0.1
    ) -> dict[str, Any] | None:
        memory_id = memory.get("id")
        if memory_id is None:
            return None
        return self.database.access_memory(int(memory_id), reinforce)

    async def add_memory(
        self,
        chat_id,
        memory,
        importance=DEFAULT_MEMORY_IMPORTANCE,
        subject: str = "",
        emotion: float = 0.0,
        valid_until: float = 0.0,
    ):
        memory = memory.strip()
        if not memory:
            return None
        importance = min(1.0, max(0.0, float(importance)))
        vector: list[float] | None = None
        meta: dict[str, Any] = {"chat_id": str(chat_id), "source": "daily_summary"}
        if valid_until:
            meta[VALID_UNTIL_KEY] = float(valid_until)
        if _has_embedding_model():
            try:
                vectors = (
                    await llm_manager.call_agent(
                        "embedding", memory, "添加记忆", app_id="memoryadd"
                    )
                ).content
                vector = vectors[0]
                _, model_id = llm_manager.embedding_identity()
                if model_id:
                    meta[EMBEDDING_MODEL_KEY] = model_id
            except (
                IndexError,
                KeyError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as exc:
                log.warning(f"记忆 embedding 生成失败，降级为无向量写入：{exc}")
                vector = None
        return self.database.add_memory(
            content=memory,
            embedding=vector or [],
            importance=importance,
            metadata=meta,
            subject=subject,
            emotion=emotion,
        )

    async def summarize(self, chat_id, history):
        # 只依赖记忆模型：向量可缺席，记忆不该因此整条写不进。
        if not llm_manager.has_slot("memory"):
            log.info("没有配置记忆模型，跳过记忆总结")
            return 0
        history_text = "\n".join(
            f"[人物ID:{node.sender if node.sender is not None else 'bot'}] "
            f"[聊天ID:{history.chat_id}] {node.time} {node.message}"
            for node in history.history
        )
        prompt = MEMORY_PROMPT.format(
            chat_id=chat_id, history=history_text, today=_today_stamp()
        )
        response = await llm_manager.call_agent(
            "memory", prompt, "Agent-记忆总结", app_id="memorysummary"
        )
        count = 0
        seen = set()
        for line in response.content.splitlines():
            memory = line.strip().lstrip("-*").strip()
            if not re.search(r"\[人物ID:[^\]]+\].*\[聊天ID:[^\]]+\]", memory):
                continue
            subject = _subject_of(memory)
            importance, emotion, memory, valid_until = _parse_memory_line(memory)
            if not memory:
                continue
            if memory in seen or count >= MAX_MEMORIES_PER_BATCH:
                continue
            seen.add(memory)
            await self.add_memory(
                chat_id,
                memory,
                importance,
                subject=subject,
                emotion=emotion,
                valid_until=valid_until,
            )
            count += 1
        return count


memory_manager = MemoryManager()


def _get_summary_dates(chat_path, last_summary_time: str | None) -> list[str]:
    today_string = today().strftime("%Y-%m-%d")
    dates = []
    for path in chat_path.glob("*.json"):
        if (
            path.stem != path.name[:-5]
            or not HISTORY_DATE_PATTERN.fullmatch(path.stem)
            or path.stem >= today_string
            or (last_summary_time is not None and path.stem <= last_summary_time)
        ):
            continue
        dates.append(path.stem)
    return sorted(dates)


def _read_last_summary_time(chat_path) -> str | None:
    path = chat_path / LAST_SUMMARY_TIME_FILE
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    if not HISTORY_DATE_PATTERN.fullmatch(value):
        log.warning(f"聊天目录 {chat_path} 的总结时间无效，重新扫描历史记录")
        return None
    return value


def _write_last_summary_time(chat_path, summary_date: str) -> None:
    path = chat_path / LAST_SUMMARY_TIME_FILE
    temporary_path = chat_path / f"{LAST_SUMMARY_TIME_FILE}.tmp"
    temporary_path.write_text(summary_date, encoding="utf-8")
    temporary_path.replace(path)


#: 每晚补算向量的上限。控制单次成本：一条一次 embedding 调用。
VECTOR_BACKFILL_PER_RUN = 50


CONSOLIDATE_PROMPT = """把下面这些零散记忆归并成少数几条。

要求：
1. 只输出记忆短句，每行一条，最多 3 条，不要标题、解释或序号。
2. 合并同一件事的重复与细节，保留真正有情绪或意义的部分。
3. 每条格式必须是 [重要度:0.0到1.0] [情绪:-1.0到1.0] [时效:YYYY-MM-DD（可选）] 内容。
   有时限的事实才写时效，永久有效的不写。
4. 这些记忆本来就关于这个人，不要再写人物 ID。

原来的记忆：
{memories}
"""

#: 每轮最多归并几个组、每组至少攒多少条才值得动手。
CONSOLIDATE_GROUPS_PER_RUN = 2
CONSOLIDATE_MIN_SIZE = 8
CONSOLIDATE_MIN_AGE_DAYS = 7.0


async def consolidate_memories() -> int:
    """把攒久了的同类记忆压成少数几条，返回被归并的原始条数。

    记忆「像人」不是记得多，而是记得概括；不压的话条数只会线性增长，
    挤占预算、稀释检索。原记忆不删，只标成已归并、退出检索。
    """
    if not llm_manager.has_slot("memory"):
        return 0
    groups = memory_manager.database.unconsolidated_groups(
        CONSOLIDATE_MIN_SIZE,
        CONSOLIDATE_GROUPS_PER_RUN,
        min_age_days=CONSOLIDATE_MIN_AGE_DAYS,
    )
    absorbed = 0
    for group in groups:
        lines = [str(item["content"]) for item in group["memories"]]
        prompt = CONSOLIDATE_PROMPT.format(
            memories="\n".join(f"- {line}" for line in lines)
        )
        try:
            response = await llm_manager.call_agent(
                "memory", prompt, "Agent-记忆归并", app_id="memorymerge"
            )
        except (ModelRequestError, OSError, RuntimeError, ValueError) as exc:
            log.warning(f"记忆归并失败，本轮中止：{exc}")
            break
        created = []
        for line in response.content.splitlines():
            body = line.strip().lstrip("-*").strip()
            if not body:
                continue
            importance, emotion, text, valid_until = _parse_memory_line(body)
            if not text:
                continue
            row = await memory_manager.add_memory(
                group["chat_id"],
                text,
                importance,
                subject=group["subject"],
                emotion=emotion,
                valid_until=valid_until,
            )
            if row:
                created.append(row["id"])
        if not created:
            continue
        absorbed += memory_manager.database.mark_consolidated(
            [item["id"] for item in group["memories"]],
            created[0],
            round_tag=_today_stamp(),
        )
    if absorbed:
        log.info(f"记忆归并：{absorbed} 条旧记忆被概括")
    return absorbed


async def backfill_vectors(limit: int = VECTOR_BACKFILL_PER_RUN) -> int:
    """给缺少可信向量的记忆补算向量，返回补了几条。

    两种情况会被补：从来没生成过向量（当时没配模型或调用失败），以及向量
    由**别的模型**生成（换个 embedding 后旧向量不可比，检索时会被排除）。
    后者正是「换了模型，历史记忆就搜不到了」的解药。

    每轮有上限，失败即停——打爆配额比晚一天补完更糟。
    """
    if not _has_embedding_model():
        return 0
    _, model_id = llm_manager.embedding_identity()
    if not model_id:
        return 0
    pending = memory_manager.database.vectors_needing_rebuild(model_id, limit)
    if not pending:
        return 0
    done = 0
    for item in pending:
        try:
            vectors = (
                await llm_manager.call_agent(
                    "embedding",
                    item["content"],
                    "补算记忆向量",
                    app_id="memoryvector",
                )
            ).content
            vector = vectors[0]
        except (
            IndexError,
            KeyError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as exc:
            log.warning(f"补算记忆向量失败，本轮中止（已补 {done} 条）：{exc}")
            break
        if memory_manager.database.update_embedding(
            item["id"], vector, model_id, expected_model=item["model"]
        ):
            done += 1
    if done:
        log.info(f"补算记忆向量 {done} 条（模型 {model_id}）")
    return done


async def summarize_memories():
    # 遗忘与总结都不该被「没配向量模型」绑架：向量只影响检索精度。
    log.info("开始遗忘记忆")
    try:
        memory_manager.database.decay_all()
    except (OSError, RuntimeError, ValueError) as exc:
        log.warning(f"记忆遗忘失败：{exc}")
    try:
        memory_manager.database.purge_expired(EXPIRED_GRACE_DAYS)
    except (OSError, RuntimeError, ValueError) as exc:
        log.warning(f"过期记忆清理失败：{exc}")
    if not llm_manager.has_slot("memory"):
        log.info("没有配置记忆模型，跳过记忆总结")
        return
    paths = history_logger.path
    log.info("开始总结记忆")
    for chat_id in os.listdir(paths):
        chat_path = paths / chat_id
        if not chat_path.is_dir():
            continue
        last_summary_time = _read_last_summary_time(chat_path)
        for summary_date in _get_summary_dates(chat_path, last_summary_time):
            history = history_logger.get_history(chat_id, summary_date)
            if history is None:
                _write_last_summary_time(chat_path, summary_date)
                continue
            total = len(history.history)
            if total == 0:
                _write_last_summary_time(chat_path, summary_date)
                continue
            start = 0
            succeeded = True
            while start < total:
                end = (
                    total
                    if total - start <= MEMORY_TAIL_SIZE
                    else min(start + MEMORY_BATCH_SIZE, total)
                )
                batch = history.model_copy(
                    update={"history": history.history[start:end]}
                )
                try:
                    await memory_manager.summarize(chat_id, batch)
                except (ModelRequestError, OSError, RuntimeError, ValueError) as exc:
                    log.error(f"聊天 {chat_id} 的记忆总结失败：{exc}")
                    succeeded = False
                    break
                if end == total:
                    break
                start += MEMORY_STEP
            if not succeeded:
                break
            _write_last_summary_time(chat_path, summary_date)
