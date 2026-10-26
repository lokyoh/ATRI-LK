import datetime
import os
import re
from typing import Any

from ATRI.log import log
from ATRI.utils.datetime import today

from ...llm import ModelRequestError, ModelType, llm_manager
from ..history import history_logger
from ..telemetry import telemetry
from .database import EMBEDDING_MODEL_KEY, MemoryDB, public_view

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
6. 为每条记忆评估两个数值：
   - **重要度**（0.0 到 1.0）：这条值不值得长期留着。宁低勿高。
   - **情绪**（-1.0 到 1.0）：这件事对她的情绪冲击有多强，负数是负面情绪。
     被夸、被逗笑接近 1.0；被冒犯、被冷落接近 -1.0；纯客观信息接近 0。
     情绪越强，这条记忆会被记得越久、越容易被突然想起。
7. 每条记忆必须原样包含两个数值、人物标记和聊天ID标记，格式必须是
    [重要度:0.0到1.0] [情绪:-1.0到1.0] [人物ID:人物id] [聊天ID:聊天id] 记忆内容
8. 人物 ID 必须来自消息前的 sender；聊天 ID 使用本段记录的 chat_id
9. 不要猜测记录中没有出现的 ID，不要把多个人物或多个事实合并成一条。
10. 如果没有符合条件的内容，只输出：无

本段群 ID：{chat_id}
聊天记录：
{history}
"""

#: 匹配 `[情绪:-0.7]`，允许负号与缺省小数位
_EMOTION_RE = re.compile(r"\[情绪\s*:\s*(-?\d+(?:\.\d+)?)\]")


def _has_embedding_model() -> bool:
    return llm_manager.has_type(ModelType.EMBEDDING)


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
        query = query.strip()
        if not query or not _has_embedding_model():
            telemetry.memory(queried=False, hits=0, degraded=True)
            return []
        try:
            vectors = (
                await llm_manager.call_model_by_type(
                    ModelType.EMBEDDING, query, "查询记忆"
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
            log.warning(f"记忆查询 embedding 生成失败，跳过检索：{exc}")
            telemetry.memory(queried=False, hits=0, degraded=True)
            return []
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
    ):
        memory = memory.strip()
        if not memory:
            return None
        importance = min(1.0, max(0.0, float(importance)))
        vector: list[float] | None = None
        meta: dict[str, Any] = {"chat_id": str(chat_id), "source": "daily_summary"}
        if _has_embedding_model():
            try:
                vectors = (
                    await llm_manager.call_model_by_type(
                        ModelType.EMBEDDING, memory, "添加记忆"
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
        if not _has_embedding_model():
            log.info("没有配置 embedding 模型，跳过记忆总结")
            return 0
        if not llm_manager.has_type(ModelType.TOOL):
            log.info("没有配置tool类型的模型，跳过记忆总结")
            return 0
        history_text = "\n".join(
            f"[人物ID:{node.sender if node.sender is not None else 'bot'}] "
            f"[聊天ID:{history.chat_id}] {node.time} {node.message}"
            for node in history.history
        )
        prompt = MEMORY_PROMPT.format(chat_id=chat_id, history=history_text)
        response = await llm_manager.call_model_by_type(
            ModelType.TOOL, prompt, "Agent-记忆总结"
        )
        count = 0
        seen = set()
        for line in response.content.splitlines():
            memory = line.strip().lstrip("-*").strip()
            importance_match = re.search(
                r"\[重要度\s*:\s*(0(?:\.\d+)?|1(?:\.0+)?)\]", memory
            )
            if not importance_match or not re.search(
                r"\[人物ID:[^\]]+\].*\[聊天ID:[^\]]+\]", memory
            ):
                continue
            importance = float(importance_match.group(1))
            emotion_match = _EMOTION_RE.search(memory)
            emotion = 0.0
            if emotion_match:
                try:
                    emotion = max(-1.0, min(1.0, float(emotion_match.group(1))))
                except ValueError:
                    emotion = 0.0
                memory = _EMOTION_RE.sub("", memory, count=1).strip()
            memory = re.sub(
                r"\[重要度\s*:\s*(?:0(?:\.\d+)?|1(?:\.0+)?)\]\s*", "", memory, count=1
            ).strip()
            if memory in seen or count >= MAX_MEMORIES_PER_BATCH:
                continue
            seen.add(memory)
            await self.add_memory(chat_id, memory, importance, emotion=emotion)
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


async def summarize_memories():
    if not _has_embedding_model():
        log.info("没有配置 embedding 模型，跳过记忆总结")
        return
    log.info("开始遗忘记忆")
    try:
        memory_manager.database.decay_all()
    except (OSError, RuntimeError, ValueError) as exc:
        log.warning(f"记忆遗忘失败：{exc}")
    if not llm_manager.has_type(ModelType.TOOL):
        log.info("没有配置tool类型的模型，跳过记忆总结")
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
