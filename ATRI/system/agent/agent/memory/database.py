import json
import math
import threading
import time
from typing import Any

from ATRI.utils.sqlite import DataBase

# Default values
DEFAULT_DECAY_RATE = 1e-7  # per-second exponential decay factor (very slow by default)

_lock = threading.RLock()


def _now() -> float:
    return time.time()


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    la = 0.0
    lb = 0.0
    dot = 0.0
    for x, y in zip(a, b):
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
    ) -> dict[str, Any]:
        if metadata is None:
            metadata = {}
        if decay_rate is None:
            decay_rate = DEFAULT_DECAY_RATE

        now = _now()
        emb_json = json.dumps(embedding)

        # Linear scan for conflicts
        candidate = None
        with _lock:
            rows = self._table.select_all()
            for row in rows:
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

                total_weight = existing_importance + importance
                if total_weight <= 0:
                    merged_embedding = [
                        (x + y) / 2.0 for x, y in zip(existing_emb, embedding)
                    ]
                else:
                    merged_embedding = [
                        (
                            existing_emb[i] * existing_importance
                            + embedding[i] * importance
                        )
                        / total_weight
                        for i in range(min(len(existing_emb), len(embedding)))
                    ]
                if len(embedding) > len(existing_emb):
                    merged_embedding.extend(embedding[len(existing_emb) :])
                elif len(existing_emb) > len(embedding):
                    merged_embedding.extend(existing_emb[len(embedding) :])

                merged_importance = max(existing_importance, importance)
                merged_meta = {**existing_meta, **(metadata or {})}

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
    ) -> list[dict[str, Any]]:
        now = _now()
        with _lock:
            rows = self._table.select_all()

        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            emb = json.loads(row[2])
            sim = _cosine_similarity(query_embedding, emb)
            if min_similarity is not None and sim < min_similarity:
                continue
            mem = self._row_to_dict(row)
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
        now = _now()
        with _lock:
            res = self._table.select("importance", {"id": mem_id})
            if not res:
                return None
            new_importance = float(res[0][0]) + float(reinforce)
            self._table.update(
                {"importance": new_importance, "last_accessed": now}, {"id": mem_id}
            )
        return self.get_memory(mem_id)

    def decay_all(self) -> None:
        now = _now()
        with _lock:
            rows = self._table.select_all("id, importance, decay_rate, created_at")
            for row in rows:
                mid = int(row[0])
                importance = float(row[1])
                decay_rate = float(row[2])
                created_at = float(row[3])
                age = now - created_at
                new_importance = importance * math.exp(-decay_rate * age)
                if new_importance < 1e-6:
                    self._table.delete({"id": mid})
                else:
                    self._table.update({"importance": new_importance}, {"id": mid})

    def all_memories(self) -> list[dict[str, Any]]:
        rows = self._table.select_all()
        return [self._row_to_dict(r) for r in rows]
