import hashlib
import os
import re
from typing import Any

from ATRI.event import Priority, daily_update
from ATRI.log import log
from ATRI.utils.datetime import today

from ...config import config
from ...llm import ModelRequestError, ModelType, llm_manager
from ...llm.rag import embedding
from ..history import history_logger
from .database import MemoryDB

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
3. 只提取适合长期保存的事实，例如稳定的个人偏好、身份信息、重要关系、长期计划、明确决定或反复出现的习惯。
4. 不要提取问候、寒暄、情绪宣泄、临时状态、普通问答、单次闲聊、重复内容或没有后续价值的消息。
5. 每条必须是独立、简短、客观的中文句子；不要把原聊天逐句改写成记忆。
6. 为每条记忆评估重要度，范围为 0.0 到 1.0：长期稳定且未来有用的信息接近 1.0，一般事实接近 0.5。
7. 每条记忆必须原样包含重要度、人物标记和群标记，格式必须是
    [重要度:0.0到1.0] [人物ID:人物id] [群ID:群id] 记忆内容
8. 人物 ID 必须来自消息前的 sender；群 ID 使用本段记录的 group_id。
9. 不要猜测记录中没有出现的 ID，不要把多个人物或多个事实合并成一条。
10. 如果没有符合条件的内容，只输出：无

本段群 ID：{group_id}
聊天记录：
{history}
"""


class MemoryManager:
    def __init__(self):
        self.database = MemoryDB()

    @staticmethod
    def _fallback_embedding(text: str, size: int = 64) -> list[float]:
        values = [0.0] * size
        for token in re.findall(r"[\w\u4e00-\u9fff]+", text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % size
            values[index] += 1.0
        return values

    async def get_memories(
        self, query: str, top_k: int, min_similarity: float | None = None
    ) -> list[dict]:
        query = query.strip()
        if not query:
            return []
        if config.embedding.enable and config.embedding.url:
            try:
                vectors = await embedding(query)
                vector = vectors[0]
            except (
                IndexError,
                KeyError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as exc:
                log.warning(f"记忆查询 embedding 生成失败，使用本地向量：{exc}")
                vector = self._fallback_embedding(query)
        else:
            vector = self._fallback_embedding(query)
        return self.database.query_by_vector(
            vector, top_k=top_k, min_similarity=min_similarity
        )

    async def access_memory(
        self, memory: dict[str, Any], reinforce: float = 0.1
    ) -> dict[str, Any] | None:
        memory_id = memory.get("id")
        if memory_id is None:
            return None
        return self.database.access_memory(int(memory_id), reinforce)

    async def add_memory(self, group_id, memory, importance=DEFAULT_MEMORY_IMPORTANCE):
        memory = memory.strip()
        if not memory:
            return None
        importance = min(1.0, max(0.0, float(importance)))
        if config.embedding.enable and config.embedding.url:
            try:
                vectors = await embedding(memory)
                vector = vectors[0]
            except (
                IndexError,
                KeyError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as exc:
                log.warning(f"记忆 embedding 生成失败，使用本地向量：{exc}")
                vector = self._fallback_embedding(memory)
        else:
            vector = self._fallback_embedding(memory)
        return self.database.add_memory(
            content=memory,
            embedding=vector,
            importance=importance,
            metadata={"group_id": str(group_id), "source": "daily_summary"},
        )

    async def summarize(self, group_id, history):
        if not llm_manager.has_type(ModelType.TOOL):
            log.warning("没有配置tool类型的模型，跳过记忆总结")
            return 0
        history_text = "\n".join(
            f"[人物ID:{node.sender if node.sender is not None else 'bot'}] "
            f"[群ID:{history.group_id}] {node.time} {node.message}"
            for node in history.history
        )
        prompt = MEMORY_PROMPT.format(group_id=group_id, history=history_text)
        response = await llm_manager.call_model_by_type(ModelType.TOOL, prompt)
        count = 0
        seen = set()
        for line in response.content.splitlines():
            memory = line.strip().lstrip("-*").strip()
            importance_match = re.search(
                r"\[重要度\s*:\s*(0(?:\.\d+)?|1(?:\.0+)?)\]", memory
            )
            if not importance_match or not re.search(
                r"\[人物ID:[^\]]+\].*\[群ID:[^\]]+\]", memory
            ):
                continue
            importance = float(importance_match.group(1))
            memory = re.sub(
                r"\[重要度\s*:\s*(?:0(?:\.\d+)?|1(?:\.0+)?)\]\s*", "", memory, count=1
            ).strip()
            if memory in seen or count >= MAX_MEMORIES_PER_BATCH:
                continue
            seen.add(memory)
            await self.add_memory(group_id, memory, importance)
            count += 1
        return count


memory_manager = MemoryManager()


def _get_summary_dates(group_path, last_summary_time: str | None) -> list[str]:
    today_string = today().strftime("%Y-%m-%d")
    dates = []
    for path in group_path.glob("*.json"):
        if (
            path.stem != path.name[:-5]
            or not HISTORY_DATE_PATTERN.fullmatch(path.stem)
            or path.stem >= today_string
            or (last_summary_time is not None and path.stem <= last_summary_time)
        ):
            continue
        dates.append(path.stem)
    return sorted(dates)


def _read_last_summary_time(group_path) -> str | None:
    path = group_path / LAST_SUMMARY_TIME_FILE
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    if not HISTORY_DATE_PATTERN.fullmatch(value):
        log.warning(f"群目录 {group_path} 的总结时间无效，重新扫描历史记录")
        return None
    return value


def _write_last_summary_time(group_path, summary_date: str) -> None:
    path = group_path / LAST_SUMMARY_TIME_FILE
    temporary_path = group_path / f"{LAST_SUMMARY_TIME_FILE}.tmp"
    temporary_path.write_text(summary_date, encoding="utf-8")
    temporary_path.replace(path)


@daily_update(priority=Priority.LOW)
async def summarize_memories():
    log.info("开始遗忘记忆")
    try:
        memory_manager.database.decay_all()
    except (OSError, RuntimeError, ValueError) as exc:
        log.warning(f"记忆遗忘失败：{exc}")
    if not llm_manager.has_type(ModelType.TOOL):
        log.warning("没有配置tool类型的模型，跳过记忆总结")
        return
    paths = history_logger.path
    log.info("开始总结记忆")
    for group_id in os.listdir(paths):
        group_path = paths / group_id
        if not group_path.is_dir():
            continue
        last_summary_time = _read_last_summary_time(group_path)
        for summary_date in _get_summary_dates(group_path, last_summary_time):
            history = history_logger.get_history(group_id, summary_date)
            if history is None:
                _write_last_summary_time(group_path, summary_date)
                continue
            total = len(history.history)
            if total == 0:
                _write_last_summary_time(group_path, summary_date)
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
                    await memory_manager.summarize(group_id, batch)
                except (ModelRequestError, OSError, RuntimeError, ValueError) as exc:
                    log.error(f"群 {group_id} 的记忆总结失败：{exc}")
                    succeeded = False
                    break
                if end == total:
                    break
                start += MEMORY_STEP
            if not succeeded:
                break
            _write_last_summary_time(group_path, summary_date)
