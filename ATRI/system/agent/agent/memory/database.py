import contextlib
import json
import math
import re
import threading
import time
from array import array
from collections import OrderedDict
from typing import Any

from ATRI.log import log
from ATRI.utils.datetime import date_fromtimestamp
from ATRI.utils.sqlite import DataBase

#: numpy 只用来批量算余弦；缺了就用纯 Python 兜底，不因此让记忆不可用。
try:
    import numpy as np
except ImportError:  # pragma: no cover - 部署环境几乎都装了
    np = None  # type: ignore[assignment]

# Default values
DEFAULT_DECAY_RATE = 1e-7  # per-second exponential decay factor (very slow by default)

#: 衰减后的重要度下限。低于它只钳住、不删除——删除由显式整理动作负责。
#: 原先的实现会在重要度跌破 1e-6 时直接 delete，配合复利衰减会让一年前的
#: 记忆在几天内全部消失（见 docs/agent-memory-design.md §一.1）。
MIN_IMPORTANCE = 1e-3

#: 记忆主体：ATRI 自己。「记忆是 ATRI 的经历」——这一维不能省。
#: 存 metadata.subject；用户相关的记忆存解析后的主账号 ID。
SELF_SUBJECT = "self"

#: 换模型后旧向量与新向量不在同一语义空间，余弦值不可比——检索时据此排除。
EMBEDDING_MODEL_KEY = "embedding_model"

#: 记忆的情绪强度（-1.0 ~ 1.0）在 metadata 里的键名。
#: 与 importance 是两件事：importance 决定「值不值得记」，
#: emotion 决定「记得多牢、会不会被突然想起」。
EMOTION_KEY = "emotion"

#: 衰减游标：上次结算到哪个时刻。放 metadata 而不是 last_accessed——
#: last_accessed 表示「最近被想起」，由同一个字段兼做衰减基准时，每天的
#: 全表结算会把前者抹成同一个值，强化信号就消失了。
DECAY_CURSOR_KEY = "decay_cursor"

#: 已被归并聚合的原始记忆。它们不再参与检索（改由聚合后的少数几条代表），
#: 但仍留在库里，随时可查、可恢复。
CONSOLIDATED_KEY = "consolidated"

#: 失效时间戳。「明天要上学」过期后仍是「明天」，这类事实一旦不退出检索，
#: 她会说出明确错误的事——比记得少更糟。
VALID_UNTIL_KEY = "valid_until"

#: 过期宽限天数。过期当场就从检索里消失，但数据保留这几天再删——
#: 抓错的时效（模型把永久事实猜成有期限）在这个窗口里还能人工救回来。
EXPIRED_GRACE_DAYS = 7.0

#: 向量缓存条数上限。997 条 1024 维约占 4 MB，无上限会随记忆线性涨；
#: 留最近 2000 条足够覆盖「常被想起的那些」。
VECTOR_CACHE_SIZE = 2000

#: 情绪强度对衰减的影响上限。
#: |emotion|=1 时衰减速率降为 (1 - 0.95) = 5%，即半衰期延长约 20 倍。
#: 不设成 1.0 是因为「永不衰减」会让高情绪记忆无限堆积、挤占预算。
EMOTION_DECAY_RESISTANCE = 0.95

#: 对外（返回给模型 / 前端）可见的字段。embedding 永不出包：
#: 一个 1536 维向量被拼进工具回填会直接顶爆上下文。
PUBLIC_FIELDS = (
    "id",
    "content",
    "importance",
    "created_at",
    "last_accessed",
    "metadata",
)

_lock = threading.RLock()

#: 内容里的聊天来源标记。当前版本写 ``[聊天ID:...]``，更早的版本写 ``[群ID:...]``。
_CHAT_TAG = re.compile(r"\[(?:聊天|群)ID\s*:\s*([^\]]+)\]")

#: 人物来源标记。``bot`` 表示她自己说的话，统一映射到 ``SELF_SUBJECT``。
_PERSON_TAG = re.compile(r"\[人物ID\s*:\s*([^\]]+)\]")


def chat_scope(metadata: dict[str, Any] | None, content: str = "") -> str:
    """一条记忆属于哪个会话，解析不出来时返回空串。

    三个来源，按可信度：

    1. ``metadata["chat_id"]``（当前版本写入）
    2. ``metadata["group_id"]``（更早版本写入——键名改过，旧数据仍在库里）
    3. 内容里的 ``[聊天ID:x]`` / ``[群ID:x]``（与 1、2 同时写入，兜底用）

    返回空串表示**没有会话归属**（真正的全局记忆，比如她自己的经历）。
    调用方若在会话内取用，必须按 ``chat_scope(...) == 会话ID`` 判断是否越界：
    读不出归属的旧数据不能当成「哪儿都能用」，那正是跨群泄漏的来源。
    """
    for key in ("chat_id", "group_id"):
        value = str((metadata or {}).get(key) or "").strip()
        if value:
            return value
    match = _CHAT_TAG.search(content or "")
    if match:
        return match.group(1).strip()
    return ""


def person_scope(metadata: dict[str, Any] | None, content: str = "") -> str:
    """一条记忆关于谁，解析不出来时返回空串。

    与 ``chat_scope`` 同构：先看 ``metadata["subject"]``，再退回内容里的
    ``[人物ID:x]``。这样历史数据（subject 还没有写入）也能按人隔离——
    用「读不出来就当哪儿都能用」来兜底，正是跨人串记忆的来源。
    """
    value = str((metadata or {}).get("subject") or "").strip()
    if value:
        return value
    match = _PERSON_TAG.search(content or "")
    if not match:
        return ""
    who = match.group(1).strip()
    return SELF_SUBJECT if who == "bot" else who


def is_expired(metadata: dict[str, Any] | None, now: float | None = None) -> bool:
    """这条记忆是否已过有效期。没有 valid_until 的永久有效。"""
    stamp = _float_or((metadata or {}).get(VALID_UNTIL_KEY), 0.0)
    if stamp <= 0:
        return False
    return stamp <= (time.time() if now is None else now)


def public_view(memory: dict[str, Any]) -> dict[str, Any]:
    """裁掉 embedding，只留模型看得懂的字段。"""
    view = {k: memory[k] for k in PUBLIC_FIELDS if k in memory}
    for key in ("similarity", "effective_importance"):
        if key in memory:
            view[key] = memory[key]
    return view


def _float_or(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _now() -> float:
    return time.time()


#: 关键词兜底的分词分隔符。没有 embedding 模型时用它把查询拆成词。
_TOKEN_SPLIT = re.compile(r"[\s,，。；;:：!！?？\-—_/|()（）\[\]【】\"'“”·]+")

#: CJK 字符：这类语言没有空格，按二字切分比整句匹配可靠。
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

#: 单次检索最多用多少个词，避免长查询把扫描成本放大。
MAX_QUERY_TOKENS = 12


def _query_tokens(query: str) -> list[str]:
    """把查询拆成用于字面匹配的词，去重保序。"""
    tokens: list[str] = []
    for part in _TOKEN_SPLIT.split(query or ""):
        part = part.strip().lower()
        if len(part) < 2:
            continue
        if _CJK_RE.search(part):
            tokens.extend(part[i : i + 2] for i in range(len(part) - 1))
        else:
            tokens.append(part)
    seen: set[str] = set()
    unique: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            unique.append(token)
    return unique[:MAX_QUERY_TOKENS]


def _row_without_embedding(row: tuple[Any, ...]) -> dict[str, Any]:
    """行 → dict，但不解析 embedding 列（筛选阶段用不到它）。"""
    return {
        "id": int(row[0]),
        "content": row[1],
        "importance": float(row[3]),
        "decay_rate": float(row[4]),
        "metadata": json.loads(row[5]) if row[5] else {},
        "created_at": float(row[6]),
        "last_accessed": float(row[7]),
    }


def _batch_similarity(query_embedding: list[float], vectors: list[Any]) -> list[float]:
    """一次性算完全部余弦相似度，维度不同的按 0 处理。

    逐条走纯 Python 循环时，1000 条 1024 维要 ~80ms，而矩阵乘只要 ~25ms；
    而真正的瓶颈其实是上游的 JSON 解析，所以这里按维度分组，把可比的
    向量一起丢给 numpy。没有 numpy 时静默退回逐条计算。
    """
    if not vectors:
        return []
    if np is None or not query_embedding:
        return [_cosine_similarity(query_embedding, vec) for vec in vectors]

    result = [0.0] * len(vectors)
    query = np.asarray(query_embedding, dtype=np.float32)
    query_norm = float(np.linalg.norm(query)) or 1.0
    unit_query = query / query_norm
    # 维度不同的向量不可比，保持 0；同维度的批量矩阵乘一次算完。
    for dim, indexes in _group_by_dim(vectors).items():
        if dim != query.shape[0]:
            continue
        matrix = np.asarray([vectors[i] for i in indexes], dtype=np.float32)
        row_norms = np.linalg.norm(matrix, axis=1)
        scored = (matrix @ unit_query) / np.where(row_norms == 0, 1.0, row_norms)
        for offset, index in enumerate(indexes):
            result[index] = float(scored[offset])
    return result


def _group_by_dim(vectors: list[list[float]]) -> dict[int, list[int]]:
    """按向量维度分组，返回 {维度: 下标列表}。"""
    groups: dict[int, list[int]] = {}
    for index, vector in enumerate(vectors):
        groups.setdefault(len(vector), []).append(index)
    return groups


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    # 维度不同说明向量来自不同 embedding 模型：此时 zip 会静默截断成前缀比较，
    # 算出看似合理的相似度。直接判定不可比，比给出错误答案安全。
    if not a or not b or len(a) != len(b):
        return 0.0
    la = 0.0
    lb = 0.0
    dot = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        la += x * x
        lb += y * y
    if la <= 0 or lb <= 0:
        return 0.0
    return dot / (math.sqrt(la) * math.sqrt(lb))


class MemoryDB:
    """Memory DB implemented on top of ATRI.utils.sqlite.DataBase (file: agent.db)

    Table: memories
    Columns (order matters for select_all results):
      id, content, embedding, importance, decay_rate, metadata, created_at, last_accessed
    """

    def __init__(self, db_name: str = "agent.db") -> None:
        # Create/open database and ensure table exists
        self._db = DataBase(db_name)
        table_content = (
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " content TEXT NOT NULL,"
            " embedding TEXT NOT NULL,"
            " importance REAL NOT NULL DEFAULT 1.0,"
            " decay_rate REAL NOT NULL DEFAULT 1e-7,"
            " metadata TEXT DEFAULT '{}',"
            " created_at REAL NOT NULL,"
            " last_accessed REAL NOT NULL"
        )
        # version=1 for initial schema
        self._table = self._db.get_table("memories", table_content, 1)
        #: 向量副表：float32 BLOB，主表 embedding 列留空。
        self._vectors_table = self._db.get_table(
            "memory_vectors",
            "id INTEGER PRIMARY KEY, model TEXT DEFAULT '', dim INTEGER DEFAULT 0,"
            " vec BLOB",
            1,
        )
        #: 解析过的向量缓存：id → 向量，超出上限按最久未用淘汰。
        self._vectors: OrderedDict[int, Any] = OrderedDict()
        self._ensure_indexes()
        self._migrate_vectors()
        self._tune_connection()

    def _parsed_vector(self, mem_id: int, legacy: str = "") -> Any:
        """取缓存里的向量；未命中就读副表（副表缺失时退回旧 JSON 列）。"""
        cache = getattr(self, "_vectors", None)
        if cache is None:
            cache = self._vectors = OrderedDict()
        if mem_id in cache:
            cache.move_to_end(mem_id)
            return cache[mem_id]
        vector = self._load_vector(mem_id, legacy)
        cache[mem_id] = vector
        while len(cache) > VECTOR_CACHE_SIZE:
            cache.popitem(last=False)
        return vector

    def _load_vector(self, mem_id: int, legacy: str = "") -> Any:
        """从副表读一条向量，float32 BLOB 直接 frombuffer，不经 JSON。"""
        table = getattr(self, "_vectors_table", None)
        res = table.select("vec, dim, model", {"id": int(mem_id)}) if table else []
        if not res or not res[0][0]:
            if legacy:
                decoded = json.loads(legacy)
                return (
                    np.asarray(decoded, dtype=np.float32) if np is not None else decoded
                )
            return np.zeros(0, dtype=np.float32) if np is not None else []
        blob = res[0][0]
        if np is None:
            return list(array.array("f", blob)) if blob else []
        dim = int(res[0][1] or 0)
        flat = np.frombuffer(blob, dtype=np.float32)
        return flat if not dim else flat.reshape(dim)

    def _vector_len(self, mem_id: int) -> int:
        """实际存下来的向量维度；没有向量时返回 0（不解析内容）。"""
        table = getattr(self, "_vectors_table", None)
        if table is None:
            return 0
        res = table.select("dim", {"id": int(mem_id)})
        return int(res[0][0] or 0) if res else 0

    def _has_trusted_vector(
        self, mem_id: int, meta: dict[str, Any], model: str
    ) -> bool:
        """这条记忆是否已有一个可信的同模型向量。"""
        if self._vector_len(mem_id) <= 0:
            return False
        tagged = str(meta.get(EMBEDDING_MODEL_KEY) or "") or self._vector_model(mem_id)
        return tagged == model

    def _vector_model(self, mem_id: int) -> str:
        """这条向量是由哪个模型生成的；没记录时返回空串。"""
        res = self._vectors_table.select("model", {"id": int(mem_id)})
        return str(res[0][0] or "") if res else ""

    def _write_vector(self, mem_id: int, vector: Any, model: str = "") -> None:
        """写一条向量到副表（整块覆盖）。行不存在时先建。"""
        values = list(vector or [])
        if np is not None:
            packed = np.asarray(values, dtype=np.float32).tobytes()
        else:
            packed = array.array("f", [float(v) for v in values]).tobytes()
        if not self._vectors_table.select("id", {"id": int(mem_id)}):
            self._vectors_table.insert({"id": int(mem_id)})
        self._vectors_table.update(
            {"model": str(model or ""), "dim": len(values), "vec": packed},
            {"id": int(mem_id)},
        )
        cache = getattr(self, "_vectors", None)
        if cache is not None:
            cache.pop(int(mem_id), None)

    def _migrate_vectors(self) -> None:
        """把主表里遗留的 JSON 向量搬进副表，搬完就清空主表那一列。

        幂等：副表已有记录的行直接跳过。搬完主表的 embedding 只留空串，
        于是 select_all 不再拖着 20 MB 文本走。
        """
        try:
            with self._db.get_exist_table("memories").get_cursor() as cursor:
                cursor.execute("SELECT id, embedding FROM memories")
                pending = [row for row in cursor.cursor.fetchall() if row[1]]
        except Exception as exc:
            log.warning(f"检查待迁移向量失败：{exc}")
            return
        if not pending:
            return
        migrated = 0
        # 整批一个事务：逐条 select+update 会让 997 行拖到十几秒。
        with self._db.get_exist_table("memory_vectors").get_cursor() as cursor:
            for mem_id, raw in pending:
                try:
                    values = json.loads(raw)
                except (TypeError, ValueError):
                    values = []
                if np is not None:
                    packed = np.asarray(values or [], dtype=np.float32).tobytes()
                else:
                    packed = array.array(
                        "f", [float(v) for v in values or []]
                    ).tobytes()
                cursor.execute(
                    "INSERT OR REPLACE INTO memory_vectors (id, dim, vec) VALUES (?, ?, ?)",
                    (int(mem_id), len(values or []), packed),
                )
                migrated += 1
        with self._db.get_exist_table("memories").get_cursor() as cursor:
            cursor.execute("UPDATE memories SET embedding = ''")
        if migrated:
            log.info(f"已把 {migrated} 条记忆向量迁到 memory_vectors（float32 BLOB）")
            self._vacuum_once()
            self._vectors.clear()

    def _vacuum_once(self) -> None:
        """迁移后回收空闲页：22 MB 的库实测能落到 4.5 MB。

        失败不影响功能——空闲页只是占空间，不占查询时间。
        """
        try:
            connection = getattr(self._db, "_connection", None)
            if connection is not None:
                connection.execute("VACUUM")
                connection.commit()
        except Exception as exc:
            log.debug(f"记忆库 VACUUM 跳过：{exc}")

    def _tune_connection(self) -> None:
        """这个负载是读多写少：mmap 让读取绕开 read() 系统调用。

        刻意**不**开 WAL——多出 -wal/-shm 两个文件会打乱现有备份脚本，
        收益不值这个麻烦。
        """
        try:
            connection = getattr(self._db, "_connection", None)
            if connection is not None:
                connection.execute("PRAGMA mmap_size = 268435456")
                connection.execute("PRAGMA cache_size = -16000")
        except Exception as exc:
            log.debug(f"记忆库 PRAGMA 调整失败（不影响功能）：{exc}")

    def _ensure_indexes(self) -> None:
        try:
            with self._db.get_exist_table("memories").get_cursor() as cursor:
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_memories_created "
                    "ON memories(created_at)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_memories_importance "
                    "ON memories(importance)"
                )
        except Exception as exc:
            log.warning(f"记忆索引创建失败：{exc}")

    def close(self) -> None:
        # underlying DataBase exposes disconnect
        self._db.disconnect()

    def _row_to_dict(self, row: tuple[Any, ...]) -> dict[str, Any]:
        """主表行 → dict。向量在副表，主表那一列只在迁移前有内容。"""
        mem_id = int(row[0])
        legacy = row[2]
        if legacy:
            try:
                vector = json.loads(legacy)
            except (TypeError, ValueError):
                vector = []
        else:
            vector = self._load_vector(mem_id)
            vector = (
                vector.tolist()
                if np is not None and hasattr(vector, "tolist")
                else list(vector)
            )
        return {
            "id": mem_id,
            "content": row[1],
            "embedding": vector,
            "importance": float(row[3]),
            "decay_rate": float(row[4]),
            "metadata": json.loads(row[5]) if row[5] else {},
            "created_at": float(row[6]),
            "last_accessed": float(row[7]),
        }

    def add_memory(
        self,
        content: str,
        embedding: list[float],
        importance: float = 1.0,
        metadata: dict[str, Any] | None = None,
        decay_rate: float | None = None,
        conflict_threshold: float = 0.9,
        merge_strategy: str = "update",
        subject: str = "",
        emotion: float = 0.0,
        valid_until: float = 0.0,
    ) -> dict[str, Any]:
        if metadata is None:
            metadata = {}
        if valid_until:
            metadata = {**metadata, VALID_UNTIL_KEY: float(valid_until)}
        if decay_rate is None:
            decay_rate = DEFAULT_DECAY_RATE

        if subject:
            metadata = {**metadata, "subject": str(subject)}
        elif "subject" not in metadata:
            metadata = {**metadata, "subject": ""}
        emotion = max(-1.0, min(1.0, float(emotion or 0.0)))
        metadata = {**metadata, EMOTION_KEY: emotion}

        now = _now()

        # Linear scan for conflicts
        candidate = None
        incoming_subject = str(metadata.get("subject") or "")
        incoming_chat = str(metadata.get("chat_id") or "")
        with _lock:
            rows = self._table.select_all()
            for row in rows:
                row_meta = json.loads(row[5]) if row[5] else {}
                if str(row_meta.get("subject") or "") != incoming_subject:
                    continue
                if str(row_meta.get("chat_id") or "") != incoming_chat:
                    continue
                existing_emb = self._load_vector(int(row[0]))
                sim = _cosine_similarity(embedding, list(existing_emb))
                if sim >= conflict_threshold:
                    candidate = (row, sim)
                    break

            if candidate is None:
                # Insert new using dict convenience
                self._table.insert(
                    {
                        "content": content,
                        "embedding": "",
                        "importance": float(importance),
                        "decay_rate": float(decay_rate),
                        "metadata": json.dumps(metadata, ensure_ascii=False),
                        "created_at": now,
                        "last_accessed": now,
                    }
                )
                # Retrieve last inserted row: select by created_at and content match is simplest
                # Note: possible collisions if identical timestamps/content exist, but acceptable for this context
                res = self._table.select(
                    "id, content, embedding, importance, decay_rate, metadata, created_at, last_accessed",
                    "content = '{}'".format(content.replace("'", "''")),
                )
                if res:
                    new_id = int(res[-1][0])
                    self._vectors_table.insert({"id": new_id, "dim": len(embedding)})
                    self._write_vector(
                        new_id, embedding, str(metadata.get(EMBEDDING_MODEL_KEY) or "")
                    )
                    res[-1] = (*res[-1][:2], "", *res[-1][3:])
                    return self._row_to_dict(res[-1])
                # fallback to scanning all and returning newest
                rows = self._table.select_all()
                newest = max(rows, key=lambda r: float(r[6]))
                self._write_vector(
                    int(newest[0]),
                    embedding,
                    str(metadata.get(EMBEDDING_MODEL_KEY) or ""),
                )
                return self._row_to_dict(newest)

            # Conflict resolution
            row, sim = candidate
            mid = int(row[0])
            existing_content = row[1]
            existing_emb = list(self._load_vector(mid))
            existing_importance = float(row[3])
            existing_meta = json.loads(row[5]) if row[5] else {}

            if merge_strategy == "skip":
                return self._row_to_dict(row)
            elif merge_strategy == "replace":
                self._table.update(
                    {
                        "content": content,
                        "embedding": "",
                        "importance": float(importance),
                        "decay_rate": float(decay_rate),
                        "metadata": json.dumps(metadata, ensure_ascii=False),
                        "last_accessed": now,
                    },
                    {"id": mid},
                )
                self._write_vector(
                    mid, embedding, str(metadata.get(EMBEDDING_MODEL_KEY) or "")
                )
                res = self._table.select(
                    "id, content, embedding, importance, decay_rate, metadata, created_at, last_accessed",
                    {"id": mid},
                )
                return self._row_to_dict(res[0])
            else:  # update / merge
                if content.strip() and content not in existing_content:
                    merged_content = existing_content + "\n[MERGED]\n" + content
                else:
                    merged_content = existing_content

                if importance > existing_importance:
                    merged_embedding = list(embedding)
                else:
                    merged_embedding = list(existing_emb)

                merged_importance = max(existing_importance, importance)
                merged_meta = {**existing_meta, **(metadata or {})}
                old_emotion = float(existing_meta.get(EMOTION_KEY) or 0.0)
                new_emotion = float(metadata.get(EMOTION_KEY) or 0.0)
                merged_meta[EMOTION_KEY] = (
                    old_emotion if abs(old_emotion) >= abs(new_emotion) else new_emotion
                )
                if content.strip() and content not in existing_content:
                    absorbed = list(merged_meta.get("absorbed") or [])
                    absorbed.append(content)
                    merged_meta["absorbed"] = absorbed[-10:]

                self._table.update(
                    {
                        "content": merged_content,
                        "embedding": "",
                        "importance": float(merged_importance),
                        "decay_rate": float(decay_rate),
                        "metadata": json.dumps(merged_meta, ensure_ascii=False),
                        "last_accessed": now,
                    },
                    {"id": mid},
                )
                self._write_vector(
                    mid,
                    merged_embedding,
                    str(merged_meta.get(EMBEDDING_MODEL_KEY) or ""),
                )
                res = self._table.select(
                    "id, content, embedding, importance, decay_rate, metadata, created_at, last_accessed",
                    {"id": mid},
                )
                return self._row_to_dict(res[0])

    def query_by_vector(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        min_similarity: float | None = None,
        apply_decay: bool = True,
        chat_id: str | None = None,
        subject: str | None = None,
        include_self: bool = True,
        embedding_model: str | None = None,
    ) -> list[dict[str, Any]]:
        """按向量检索。

        :param chat_id: 会话隔离。给定后只返回**本会话**与**全局**（metadata 无
            chat_id）的记忆。为 None 时不做过滤——仅限后台/运维用途，
            对话链路必须传，否则 A 群的事实会被 B 群检索到。
        :param subject: 「关于谁」。给定后只返回该主体的记忆。
        :param include_self: 是否把 ``subject == SELF_SUBJECT``（亚托莉自己的
            经历）也算进来。自己的记忆不涉及他人隐私，默认放开。
        :param embedding_model: 当前 embedding 模型的真实 id。给定后，
            **由别的模型生成的向量不参与打分**——不同语义空间的余弦值
            看着合理、实际无意义，混算等于随机召回。
            标识为空（老数据 / 换模型前的记录）同样排除：无法证明可比，
            就当作不可比。
        """
        top_k = max(1, min(int(top_k), 20))
        if min_similarity is not None:
            min_similarity = min(1.0, max(0.0, float(min_similarity)))
        scope = str(chat_id) if chat_id is not None else None
        who = str(subject) if subject is not None else None
        model_id = str(embedding_model) if embedding_model else None
        now = _now()
        with _lock:
            rows = self._table.select_all()

        # 先按元数据筛掉不要的行，再解析 embedding：解析 JSON 向量是全表扫描里
        # 最贵的一步（997 行实测 0.35s，约占七成），为用不上的行付这笔钱没意义。
        survivors: list[dict[str, Any]] = []
        legacy_vectors: list[str] = []
        for row in rows:
            mem = _row_without_embedding(row)
            meta = mem["metadata"]
            if scope is not None:
                # 旧数据用 group_id（键名改过），也有只带内容标记的，
                # 一律按 chat_scope 解析；解析不出来才算真正的全局记忆。
                row_chat = chat_scope(meta, mem["content"])
                if row_chat and row_chat != scope:
                    continue
            if who is not None:
                # 历史行的 subject 是空的，按内容里的 [人物ID] 兜底解析，
                # 否则这些记忆对谁都可见。
                row_subject = person_scope(meta, mem["content"])
                if (
                    row_subject
                    and row_subject != who
                    and not (include_self and row_subject == SELF_SUBJECT)
                ):
                    continue
            if meta.get(CONSOLIDATED_KEY):
                # 已被概括过的原记忆退出检索，改由聚合后的那条代表它。
                continue
            if is_expired(meta, now):
                # 过期的事实留着只会让她说错话，不删但要退出检索。
                continue
            if model_id is not None:
                tagged = str(meta.get(EMBEDDING_MODEL_KEY) or "")
                if not tagged:
                    tagged = self._vector_model(mem["id"])
                if tagged != model_id:
                    continue
            survivors.append(mem)
            legacy_vectors.append(row[2] or "")

        if not survivors:
            return []
        vectors = [
            self._parsed_vector(mem["id"], legacy)
            for mem, legacy in zip(survivors, legacy_vectors, strict=True)
        ]
        sims = _batch_similarity(query_embedding, vectors)

        scored: list[tuple[float, dict[str, Any]]] = []
        for mem, vector, sim in zip(survivors, vectors, sims, strict=True):
            mem["embedding"] = vector.tolist() if np is not None else list(vector)
            if min_similarity is not None and sim < min_similarity:
                continue
            if apply_decay:
                age = now - mem["created_at"]
                effective = mem["importance"] * math.exp(-mem["decay_rate"] * age)
            else:
                effective = mem["importance"]
            mem["similarity"] = sim
            mem["effective_importance"] = effective
            scored.append((sim * (1.0 + effective), mem))

        scored.sort(key=lambda t: t[0], reverse=True)
        return [m for _, m in scored[:top_k]]

    def query_by_lexical(
        self,
        query: str,
        top_k: int = 5,
        apply_decay: bool = True,
        chat_id: str | None = None,
        subject: str | None = None,
        include_self: bool = True,
    ) -> list[dict[str, Any]]:
        """关键词兜底检索：没有 embedding 模型时用，按字面命中数与重要度排序。

        精度不如向量检索，但比「查不到」好：没配向量模型的部署至少还能由
        关键词想起相关的事。``similarity`` 用命中词占比表示，口径与向量一致。
        """
        top_k = max(1, min(int(top_k), 20))
        tokens = _query_tokens(query)
        if not tokens:
            return []
        scope = str(chat_id) if chat_id is not None else None
        who = str(subject) if subject is not None else None
        now = _now()
        with _lock:
            rows = self._table.select_all()

        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            # 关键词检索不需要向量，省掉这一步 JSON 解析。
            mem = _row_without_embedding(row)
            content = str(mem["content"] or "").lower()
            if not content:
                continue
            meta = mem["metadata"] or {}
            if scope is not None:
                row_chat = chat_scope(meta, mem["content"])
                if row_chat and row_chat != scope:
                    continue
            if who is not None:
                row_subject = person_scope(meta, mem["content"])
                if (
                    row_subject
                    and row_subject != who
                    and not (include_self and row_subject == SELF_SUBJECT)
                ):
                    continue
            if meta.get(CONSOLIDATED_KEY):
                continue
            if is_expired(meta, now):
                continue
            hits = sum(1 for token in tokens if token in content)
            if not hits:
                continue
            similarity = hits / len(tokens)
            if apply_decay:
                age = now - mem["created_at"]
                effective = mem["importance"] * math.exp(-mem["decay_rate"] * age)
            else:
                effective = mem["importance"]
            mem["similarity"] = similarity
            mem["effective_importance"] = effective
            scored.append((similarity * (1.0 + effective), mem))

        scored.sort(key=lambda t: t[0], reverse=True)
        return [m for _, m in scored[:top_k]]

    def vectors_needing_rebuild(
        self, model_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """找出向量不可信的行：没有向量，或由别的模型生成的。

        标识为空的历史数据一律算不可信——无法证明可比就当不可比，与检索时
        的过滤口径一致。按「重要度高、最近被想起」优先排序：先修最常用的。
        """
        limit = max(1, int(limit))
        wanted = str(model_id or "")
        with _lock:
            rows = self._table.select_all(
                "id, content, importance, last_accessed, metadata"
            )
        pending: list[tuple[float, float, dict[str, Any]]] = []
        for row in rows:
            meta = json.loads(row[4]) if row[4] else {}
            mem_id = int(row[0])
            if wanted and self._has_trusted_vector(mem_id, meta, wanted):
                continue
            pending.append(
                (
                    float(row[2]),
                    float(row[3] or 0.0),
                    {
                        "id": int(row[0]),
                        "content": row[1],
                        "model": str(meta.get(EMBEDDING_MODEL_KEY) or ""),
                        "metadata": meta,
                    },
                )
            )
        pending.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in pending[:limit]]

    def count_vectors_needing_rebuild(self, model_id: str) -> int:
        """待补向量的条数，口径与 ``vectors_needing_rebuild`` 一致。"""
        wanted = str(model_id or "")
        with _lock:
            rows = self._table.select_all("id, metadata")
        count = 0
        for row in rows:
            meta = json.loads(row[1]) if row[1] else {}
            if wanted and self._has_trusted_vector(int(row[0]), meta, wanted):
                continue
            count += 1
        return count

    def update_embedding(
        self,
        mem_id: int,
        vector: list[float],
        model_id: str,
        expected_model: str = "",
    ) -> bool:
        """补一条记忆的向量。带上旧标识做乐观锁，避免并发重复计费。

        补算是后台动作，不算「被想起」：不动 last_accessed。
        """
        with _lock:
            res = self._table.select("metadata", {"id": mem_id})
            if not res:
                return False
            meta = json.loads(res[0][0]) if res[0][0] else {}
            tagged = str(meta.get(EMBEDDING_MODEL_KEY) or "")
            if not tagged:
                tagged = self._vector_model(int(mem_id))
            if tagged != str(expected_model or ""):
                return False
            meta[EMBEDDING_MODEL_KEY] = str(model_id)
            self._table.update(
                {"metadata": json.dumps(meta, ensure_ascii=False)},
                {"id": mem_id},
            )
            self._write_vector(int(mem_id), vector, model_id)
        return True

    def unconsolidated_groups(
        self,
        min_size: int,
        limit: int,
        *,
        min_age_days: float = 0.0,
    ) -> list[dict[str, Any]]:
        """找出攒够条数的 (会话, 主体) 组，按「最久未整理」优先。

        人不会同时保留「她昨天说怕冷」「她前天说怕冷」——记住的是一个印象。
        这里只负责分组与挑候选，怎么概括交给模型。
        """
        cutoff = _now() - float(min_age_days) * 86400.0
        with _lock:
            rows = self._table.select_all()
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in rows:
            mem = _row_without_embedding(row)
            meta = mem["metadata"] or {}
            if meta.get(CONSOLIDATED_KEY):
                continue
            if mem["created_at"] > cutoff:
                continue
            if is_expired(meta):
                continue
            key = (
                chat_scope(meta, mem["content"]),
                person_scope(meta, mem["content"]),
            )
            groups.setdefault(key, []).append(mem)
        ready = [
            {
                "chat_id": chat,
                "subject": who,
                "memories": sorted(items, key=lambda m: m["created_at"]),
            }
            for (chat, who), items in groups.items()
            if len(items) >= max(2, int(min_size))
        ]
        ready.sort(key=lambda g: g["memories"][-1]["created_at"])
        return ready[: max(1, int(limit))]

    def mark_consolidated(
        self, ids: list[int], summary_id: int, *, round_tag: str = ""
    ) -> int:
        """把被概括掉的原记忆标成已归并：退出检索，但留在库里可查。"""
        marked = 0
        with _lock:
            for mem_id in ids:
                res = self._table.select("metadata", {"id": int(mem_id)})
                if not res:
                    continue
                meta = json.loads(res[0][0]) if res[0][0] else {}
                meta[CONSOLIDATED_KEY] = summary_id
                if round_tag:
                    meta["consolidated_at"] = round_tag
                self._table.update(
                    {"metadata": json.dumps(meta, ensure_ascii=False)},
                    {"id": int(mem_id)},
                )
                marked += 1
        return marked

    def get_memory(self, mem_id: int) -> dict[str, Any] | None:
        res = self._table.select(
            "id, content, embedding, importance, decay_rate, metadata, created_at, last_accessed",
            {"id": mem_id},
        )
        if not res:
            return None
        return self._row_to_dict(res[0])

    def access_memory(
        self, mem_id: int, reinforce: float = 0.1
    ) -> dict[str, Any] | None:
        """采用并强化一条记忆。

        ``last_accessed`` 同时被前移：它既是「最近使用」的展示字段，也是
        增量衰减的基准（见 ``decay_all``）。不一起前移的话，一条被反复
        采用的记忆反而会因为 ``last_accessed`` 停滞而继续掉分。
        """
        now = _now()
        with _lock:
            res = self._table.select("importance, metadata", {"id": mem_id})
            if not res:
                return None
            new_importance = min(1.0, float(res[0][0]) + float(reinforce))
            meta = json.loads(res[0][1]) if res[0][1] else {}
            # 被反复想起的记忆要顺带把衰减游标也前移：否则下次结算会把
            # 「这几天没衰减」记在它头上，刚加的强化立刻被吃掉。
            meta[DECAY_CURSOR_KEY] = now
            self._table.update(
                {
                    "importance": new_importance,
                    "last_accessed": now,
                    "metadata": json.dumps(meta, ensure_ascii=False),
                },
                {"id": mem_id},
            )
        return self.get_memory(mem_id)

    def decay_all(self) -> None:
        """每轮遗忘：**增量**衰减，只掉「上一次衰减到现在」这一段。

        旧实现用 ``importance * exp(-decay_rate * (now - created_at))``，
        而 ``importance`` 本身已是衰减后的值，等于把同一段衰减反复累乘，
        再配上「跌破 1e-6 就 delete」，一年前的记忆跑几次日任务就没了。
        这里改用 ``last_accessed`` 作为增量基准：每天只衰减过去一天，
        并同时前移 ``last_accessed``，使下一次衰减不会重复计算同一段区间。

        P3 增加一层：**情绪强度调制衰减速率**。
        ``|emotion|`` 越高的记忆掉得越慢（被冒犯的事记很久），
        平淡的事实快速淡出——这是「像人」和「像档案」的分界。
        """
        now = _now()
        span_days = 0.0
        changed = 0
        with _lock:
            rows = self._table.select_all(
                "id, importance, decay_rate, last_accessed, metadata"
            )
            for row in rows:
                mid = int(row[0])
                importance = float(row[1])
                decay_rate = float(row[2])
                last_accessed = float(row[3]) if row[3] is not None else now
                meta = json.loads(row[4]) if row[4] else {}
                emotion = abs(float(meta.get(EMOTION_KEY) or 0.0))
                effective_rate = decay_rate * (
                    1.0 - EMOTION_DECAY_RESISTANCE * min(1.0, emotion)
                )
                cursor = _float_or(meta.get(DECAY_CURSOR_KEY), last_accessed)
                elapsed = max(0.0, now - cursor)
                span_days = max(span_days, elapsed / 86400.0)
                new_importance = max(
                    MIN_IMPORTANCE, importance * math.exp(-effective_rate * elapsed)
                )
                meta[DECAY_CURSOR_KEY] = now
                self._table.update(
                    {
                        "importance": new_importance,
                        "metadata": json.dumps(meta, ensure_ascii=False),
                    },
                    {"id": mid},
                )
                changed += 1
        if changed:
            try:
                from ..telemetry import telemetry

                telemetry.decay(count=changed, span_days=round(span_days, 2))
            except Exception:
                pass

    def purge_expired(self, grace_days: float = EXPIRED_GRACE_DAYS) -> int:
        """删掉过期超过宽限期的记忆，返回删除条数。

        过期当场就退出检索了（见 `query_by_vector` / `query_by_lexical`），
        但行一直留着：不删就等于数据库只增不减。宽限期的作用是给「时效写错」
        的记忆留一条命——模型把稳定偏好误判成有期限时，还能在到期后几天内
        从 WebUI 看到并手工救回来。

        连向量副表一起清，否则只剩一堆没有主表的孤儿向量。
        """
        now = _now()
        cutoff = now - max(0.0, float(grace_days)) * 86400.0
        expired_ids: list[int] = []
        with _lock:
            rows = self._table.select_all("id, metadata")
            for row in rows:
                meta = json.loads(row[1]) if row[1] else {}
                stamp = _float_or(meta.get(VALID_UNTIL_KEY), 0.0)
                # stamp <= 0 是永久有效，不参与清理。
                if stamp > 0 and stamp <= cutoff:
                    expired_ids.append(int(row[0]))
            if not expired_ids:
                return 0
            for mem_id in expired_ids:
                self._table.delete({"id": mem_id})
                with contextlib.suppress(Exception):
                    self._vectors_table.delete({"id": mem_id})
                cache = getattr(self, "_vectors", None)
                if cache is not None:
                    cache.pop(mem_id, None)
        if expired_ids:
            log.info(f"清理了 {len(expired_ids)} 条过期记忆")
        return len(expired_ids)

    def all_memories(self) -> list[dict[str, Any]]:
        rows = self._table.select_all()
        return [self._row_to_dict(r) for r in rows]

    def list_rows(self) -> list[dict[str, Any]]:
        """不带向量的全部行；管理端浏览用，不解析 embedding。"""
        rows = self._table.select_all()
        return [_row_without_embedding(r) for r in rows]

    def memories_by_date(self, date: str) -> list[dict[str, Any]]:
        """按自然日取记忆（日程生成用），不解析 embedding。"""
        rows = self._table.select(
            "id, content, importance, metadata, created_at, last_accessed",
            "1 = 1",
        )
        result = []
        for row in rows:
            if date_fromtimestamp(float(row[4])).isoformat() != date:
                continue
            result.append(
                {
                    "id": int(row[0]),
                    "content": row[1],
                    "importance": float(row[2]),
                    "metadata": json.loads(row[3]) if row[3] else {},
                    "created_at": float(row[4]),
                    "last_accessed": float(row[5]),
                }
            )
        return result
