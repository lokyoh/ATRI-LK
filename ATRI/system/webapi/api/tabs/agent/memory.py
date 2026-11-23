"""长期记忆浏览 API：只看不改，供 WebUI 排查「她记住了什么、为什么忘了」。"""

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


__all__ = ["MemoryItem", "MemoryPage", "router"]
