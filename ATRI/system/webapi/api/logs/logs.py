import json
from collections.abc import Callable

from fastapi import APIRouter
from starlette.websockets import WebSocket

from ...ws_publisher import serve_publisher
from .log_manager import BOOT_ID, LOG_STORAGE, LogEntry

router = APIRouter()

LOG_QUEUE_MAX = 200
"""单连接待发送队列上限：慢客户端丢最旧日志，绝不阻塞其他订阅者"""


def _serialize(entry: LogEntry) -> str:
    return json.dumps(
        {
            "boot": BOOT_ID,
            "seq": entry.seq,
            "time": entry.time.strftime("%H:%M:%S"),
            "level": entry.level,
            "text": entry.text,
        }
    )


@router.websocket("/logs")
async def system_logs_realtime(websocket: WebSocket):
    def build(handshake: dict, enqueue: Callable[[str], None]) -> Callable[[], None]:
        # 握手报文：{"token": <JWT>, "boot": <上次后端标识>, "since": <已收最大 seq>}
        # boot 与当前一致才按 since 续传；后端重启（boot 变化）则全量回放，
        # 避免 seq 空间重置后客户端拿着旧断点补不到日志或收到错位回放
        since = 0
        if handshake.get("boot") == BOOT_ID:
            try:
                since = int(handshake.get("since") or 0)
            except (TypeError, ValueError):
                since = 0

        # 先灌历史回放、再订阅实时流，保证顺序且无丢失窗口
        for entry in LOG_STORAGE.get_recent(since=since):
            enqueue(json.dumps(_serialize(entry)))

        def on_entry(entry: LogEntry) -> None:
            enqueue(json.dumps(_serialize(entry)))

        LOG_STORAGE.listeners.add(on_entry)
        return lambda: LOG_STORAGE.listeners.discard(on_entry)

    await serve_publisher(websocket, build, queue_max=LOG_QUEUE_MAX)
