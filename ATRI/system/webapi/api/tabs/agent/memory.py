from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ATRI.exceptions import str_traceback
from ATRI.log import log
from ATRI.system.agent.agent.memory.database import (
    CONSOLIDATED_KEY,
    EMBEDDING_MODEL_KEY,
    EMOTION_KEY,
    VALID_UNTIL_KEY,
    chat_scope,
    is_expired,
    person_scope,
)

from ....base_model import Result
from ....security import authentication

router = APIRouter()

MAX_PAGE_SIZE = 200


class MemoryItem(BaseModel):
    id: int
    content: str
    importance: float
    """当前重要度（库里的值，已经按增量衰减结算过）"""
    decay_rate: float
    emotion: float = 0.0
    created_at: float
    last_accessed: float
    chat_id: str = ""
    """记忆归属的会话；空表示全局"""
    subject: str = ""
    """记忆主体：「self」是亚托莉自己，其余是账号 ID"""
    consolidated: bool = False
    """已被归并聚合，退出检索但仍在库里"""
    expired: bool = False
    """过了 valid_until，不再参与检索"""
    valid_until: float = 0
    embedding_model: str = ""
    """有向量时记录生成它的模型；换模型后旧向量不参与打分"""


class MemoryPage(BaseModel):
    total: int
    """筛选后的总条数"""
    items: list[MemoryItem]
    chats: list[str] = []
    """出现过的会话 ID，供下拉筛选"""
    subjects: list[str] = []


def _to_item(row: dict) -> MemoryItem:
    meta = row.get("metadata") or {}
    model_id = str(meta.get(EMBEDDING_MODEL_KEY) or "")
    return MemoryItem(
        id=int(row["id"]),
        content=str(row.get("content") or ""),
        importance=float(row.get("importance") or 0.0),
        decay_rate=float(row.get("decay_rate") or 0.0),
        emotion=float(meta.get(EMOTION_KEY) or 0.0),
        created_at=float(row.get("created_at") or 0.0),
        last_accessed=float(row.get("last_accessed") or 0.0),
        chat_id=chat_scope(meta, str(row.get("content") or "")),
        subject=person_scope(meta, str(row.get("content") or "")),
        consolidated=bool(meta.get(CONSOLIDATED_KEY)),
        expired=is_expired(meta),
        valid_until=float(meta.get(VALID_UNTIL_KEY) or 0.0),
        embedding_model=model_id,
    )


def _created_date(item: MemoryItem) -> str:
    return datetime.fromtimestamp(item.created_at).date().isoformat()


@router.get(
    "/get_memories",
    dependencies=[authentication()],
    response_model=Result[MemoryPage],
    response_class=JSONResponse,
    description="分页浏览长期记忆（可按内容、会话、主体、日期筛选）",
)
async def _(
    keyword: str = "",
    chat_id: str = "",
    subject: str = "",
    date: str = "",
    offset: int = 0,
    limit: int = 50,
    include_consolidated: bool = False,
    include_expired: bool = True,
) -> Result[MemoryPage]:
    try:
        from ATRI.system.agent.agent.memory.manage import memory_manager

        rows = memory_manager.database.list_rows()
        items = [_to_item(row) for row in rows]

        # 下拉候选取自全量，不受当前筛选影响，否则筛过一轮就选不回去了。
        chats = sorted({item.chat_id for item in items if item.chat_id})
        subjects = sorted({item.subject for item in items if item.subject})

        key = keyword.strip().lower()
        if key:
            items = [item for item in items if key in item.content.lower()]
        if chat_id:
            items = [item for item in items if item.chat_id == chat_id]
        if subject:
            items = [item for item in items if item.subject == subject]
        if date:
            items = [item for item in items if _created_date(item) == date]
        if not include_consolidated:
            items = [item for item in items if not item.consolidated]
        if not include_expired:
            items = [item for item in items if not item.expired]

        items.sort(key=lambda item: item.created_at, reverse=True)
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), MAX_PAGE_SIZE))
        return Result.ok(
            MemoryPage(
                total=len(items),
                items=items[offset : offset + limit],
                chats=chats,
                subjects=subjects,
            ),
            "拿到信息啦!",
        )
    except Exception as e:
        log.error(f"{router.prefix}/get_memories 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.get(
    "/get_memory_stats",
    dependencies=[authentication()],
    response_model=Result[dict],
    response_class=JSONResponse,
    description="长期记忆概览：总数、各会话条数、向量缺失与过期情况",
)
async def _() -> Result[dict]:
    try:
        from ATRI.system.agent.agent.memory.manage import memory_manager

        items = [_to_item(row) for row in memory_manager.database.list_rows()]
        by_chat: dict[str, int] = {}
        for item in items:
            by_chat[item.chat_id or "(全局)"] = (
                by_chat.get(item.chat_id or "(全局)", 0) + 1
            )
        return Result.ok(
            {
                "total": len(items),
                "self": sum(1 for item in items if item.subject == "self"),
                "consolidated": sum(1 for item in items if item.consolidated),
                "expired": sum(1 for item in items if item.expired),
                "without_vector": sum(1 for item in items if not item.embedding_model),
                "by_chat": [
                    {"chat_id": key, "count": value}
                    for key, value in sorted(by_chat.items(), key=lambda kv: -kv[1])
                ],
            },
            "拿到信息啦!",
        )
    except Exception as e:
        log.error(f"{router.prefix}/get_memory_stats 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


class MemoryVectorBackfill(BaseModel):
    filled: int
    """本次补算成功的条数"""
    pending: int
    """补算后仍未补齐的条数"""
    stopped: bool
    """中途失败被中止"""
    reason: str = ""
    """中止原因"""


class MemoryVectorStatus(BaseModel):
    has_model: bool
    """是否配置了可用的向量模型"""
    model_id: str
    """当前向量模型的真实 id；空表示还没解析出来"""
    pending: int
    """向量不可信（缺失或来自别的模型）的条数"""


def _vector_identity() -> tuple[bool, str]:
    from ATRI.system.agent.agent.memory.manage import llm_manager

    if not llm_manager.has_slot("embedding"):
        return False, ""
    _, model_id = llm_manager.embedding_identity()
    return True, str(model_id or "")


@router.get(
    "/get_memory_vector_status",
    dependencies=[authentication()],
    response_model=Result[MemoryVectorStatus],
    response_class=JSONResponse,
    description="向量补齐进度：有没有向量模型、还剩多少条待补",
)
async def _() -> Result[MemoryVectorStatus]:
    try:
        from ATRI.system.agent.agent.memory.manage import memory_manager

        has_model, model_id = _vector_identity()
        pending = (
            memory_manager.database.count_vectors_needing_rebuild(model_id)
            if has_model and model_id
            else 0
        )
        return Result.ok(
            MemoryVectorStatus(has_model=has_model, model_id=model_id, pending=pending),
            "拿到信息啦!",
        )
    except Exception as e:
        log.error(
            f"{router.prefix}/get_memory_vector_status 调用错误:{str_traceback(e)}"
        )
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/backfill_memory_vectors",
    dependencies=[authentication()],
    response_model=Result[MemoryVectorBackfill],
    response_class=JSONResponse,
    description="手动补算一批记忆向量（一次一条 embedding 调用，按重要度优先）",
)
async def _(limit: int = 20) -> Result[MemoryVectorBackfill]:
    try:
        from ATRI.system.agent.agent.memory.manage import (
            VECTOR_BACKFILL_PER_RUN,
            llm_manager,
            memory_manager,
        )

        has_model, model_id = _vector_identity()
        if not has_model:
            return Result.fail("没有配置向量模型，无法补算向量", 400)
        if not model_id:
            return Result.fail("向量模型还没解析出标识，请先确认模型已注册", 400)

        limit = max(1, min(int(limit), VECTOR_BACKFILL_PER_RUN))
        pending_before = memory_manager.database.vectors_needing_rebuild(
            model_id, limit=limit
        )
        filled = 0
        stopped = False
        reason = ""

        for item in pending_before:
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
            except Exception as exc:
                stopped = True
                reason = f"{type(exc).__name__}: {exc}"
                log.warning(f"补算记忆向量中止（已补 {filled} 条）：{reason}")
                break
            if memory_manager.database.update_embedding(
                item["id"], vector, model_id, expected_model=item["model"]
            ):
                filled += 1

        remaining = memory_manager.database.count_vectors_needing_rebuild(model_id)
        info = f"补算完成：本次 {filled} 条，还剩 {remaining} 条"
        return Result.ok(
            MemoryVectorBackfill(
                filled=filled, pending=remaining, stopped=stopped, reason=reason
            ),
            info,
        )
    except Exception as e:
        log.error(
            f"{router.prefix}/backfill_memory_vectors 调用错误:{str_traceback(e)}"
        )
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


__all__ = [
    "MemoryItem",
    "MemoryPage",
    "MemoryVectorBackfill",
    "MemoryVectorStatus",
    "router",
]
