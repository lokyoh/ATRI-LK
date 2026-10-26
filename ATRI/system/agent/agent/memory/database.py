import json
import math
import threading
import time
from typing import Any

from ATRI.log import log
from ATRI.utils.datetime import date_fromtimestamp
from ATRI.utils.sqlite import DataBase

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


def public_view(memory: dict[str, Any]) -> dict[str, Any]:
    """裁掉 embedding，只留模型看得懂的字段。"""
    view = {k: memory[k] for k in PUBLIC_FIELDS if k in memory}
    for key in ("similarity", "effective_importance"):
        if key in memory:
            view[key] = memory[key]
    return view


def _now() -> float:
    return time.time()


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
        self._ensure_indexes()

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
        return {
            "id": int(row[0]),
            "content": row[1],
            "embedding": json.loads(row[2]),
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
    ) -> dict[str, Any]:
        if metadata is None:
            metadata = {}
        if decay_rate is None:
            decay_rate = DEFAULT_DECAY_RATE

        if subject:
            metadata = {**metadata, "subject": str(subject)}
        elif "subject" not in metadata:
            metadata = {**metadata, "subject": ""}
        emotion = max(-1.0, min(1.0, float(emotion or 0.0)))
        metadata = {**metadata, EMOTION_KEY: emotion}

        now = _now()
        emb_json = json.dumps(embedding) if embedding else "[]"

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
                existing_emb = json.loads(row[2])
                sim = _cosine_similarity(embedding, existing_emb)
                if sim >= conflict_threshold:
                    candidate = (row, sim)
                    break

            if candidate is None:
                # Insert new using dict convenience
                self._table.insert(
                    {
                        "content": content,
                        "embedding": emb_json,
                        "importance": float(importance),
                        "decay_rate": float(decay_rate),
                        "metadata": json.dumps(metadata),
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
                    return self._row_to_dict(res[-1])
                # fallback to scanning all and returning newest
                rows = self._table.select_all()
                newest = max(rows, key=lambda r: float(r[6]))
                return self._row_to_dict(newest)

            # Conflict resolution
            row, sim = candidate
            mid = int(row[0])
            existing_content = row[1]
            existing_emb = json.loads(row[2])
            existing_importance = float(row[3])
            existing_meta = json.loads(row[5]) if row[5] else {}

            if merge_strategy == "skip":
                return self._row_to_dict(row)
            elif merge_strategy == "replace":
                self._table.update(
                    {
                        "content": content,
                        "embedding": emb_json,
                        "importance": float(importance),
                        "decay_rate": float(decay_rate),
                        "metadata": json.dumps(metadata),
                        "last_accessed": now,
                    },
                    {"id": mid},
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
                        "embedding": json.dumps(merged_embedding),
                        "importance": float(merged_importance),
                        "decay_rate": float(decay_rate),
                        "metadata": json.dumps(merged_meta),
                        "last_accessed": now,
                    },
                    {"id": mid},
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

        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            mem = self._row_to_dict(row)
            meta = mem["metadata"] or {}
            if scope is not None:
                row_chat = str(meta.get("chat_id") or "")
                if row_chat and row_chat != scope:
                    continue
            if who is not None:
                row_subject = str(meta.get("subject") or "")
                if (
                    row_subject
                    and row_subject != who
                    and not (include_self and row_subject == SELF_SUBJECT)
                ):
                    continue
            if model_id is not None:
                row_model = str(meta.get(EMBEDDING_MODEL_KEY) or "")
                if row_model != model_id:
                    continue
            sim = _cosine_similarity(query_embedding, mem["embedding"])
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
            res = self._table.select("importance", {"id": mem_id})
            if not res:
                return None
            new_importance = min(1.0, float(res[0][0]) + float(reinforce))
            self._table.update(
                {"importance": new_importance, "last_accessed": now}, {"id": mem_id}
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
                elapsed = max(0.0, now - last_accessed)
                span_days = max(span_days, elapsed / 86400.0)
                new_importance = max(
                    MIN_IMPORTANCE, importance * math.exp(-effective_rate * elapsed)
                )
                self._table.update(
                    {"importance": new_importance, "last_accessed": now},
                    {"id": mid},
                )
                changed += 1
        if changed:
            try:
                from ..telemetry import telemetry

                telemetry.decay(count=changed, span_days=round(span_days, 2))
            except Exception:
                pass

    def all_memories(self) -> list[dict[str, Any]]:
        rows = self._table.select_all()
        return [self._row_to_dict(r) for r in rows]

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
