"""WebSocket 推送端点的公共样板。

logs / system_status 等推送型端点结构完全一致：
握手鉴权 → accept → 有界队列（满丢最旧）→ sender task → receive 循环 → 清理。
新端点只需实现 build(handshake, enqueue) 返回退订函数，
队列管理、断开检测与清理全部由 serve_publisher 接管。
"""

import asyncio
import contextlib
from collections.abc import Callable

from starlette.websockets import WebSocket, WebSocketDisconnect

from .security import ws_auth


async def serve_publisher(
    websocket: WebSocket,
    build: Callable[[dict, Callable[[str], None]], Callable[[], None]],
    queue_max: int = 200,
) -> None:
    """握手鉴权后接管连接，把 build 产生的消息推给客户端直到断开。

    参数:
        websocket: 连接
        build: 握手通过后调用一次，入参为（握手报文, enqueue）；
            完成历史回放灌充与订阅注册，返回退订函数（断开时必然调用）。
        queue_max: 单连接待发送队列上限，满了丢最旧，慢客户端不阻塞广播方。
    """
    handshake = await ws_auth(websocket)
    if handshake is None:
        return

    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=queue_max)

    def enqueue(message: str) -> None:
        # 满了丢最旧：洪峰下队列不涨内存，客户端始终偏向拿到新消息
        if queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
        queue.put_nowait(message)

    detach = build(handshake, enqueue)

    async def sender() -> None:
        while True:
            message = await queue.get()
            await websocket.send_text(message)

    sender_task = asyncio.create_task(sender())
    try:
        while True:
            recv = await websocket.receive()
            if recv["type"] == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    finally:
        detach()
        sender_task.cancel()
        with contextlib.suppress(
            asyncio.CancelledError, WebSocketDisconnect, RuntimeError
        ):
            await sender_task
