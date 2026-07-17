from datetime import datetime

import nonebot
from fastapi import APIRouter
from nonebot import on_message
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from ....config import AVA_URL
from .model import Message, MessageItem

driver = nonebot.get_driver()

ws_conn: WebSocket | None = None

ID2NAME = {}

ID_LIST = []

ws_router = APIRouter()


# matcher = on_message(block=False, priority=1, rule=lambda: bool(ws_conn))


@driver.on_shutdown
async def _():
    if ws_conn and ws_conn.client_state == WebSocketState.CONNECTED:
        await ws_conn.close()


@ws_router.websocket("/chat")
async def _(websocket: WebSocket):
    global ws_conn
    await websocket.accept()
    if not ws_conn or ws_conn.client_state != WebSocketState.CONNECTED:
        ws_conn = websocket
        try:
            while websocket.client_state == WebSocketState.CONNECTED:
                await websocket.receive()
        except WebSocketDisconnect:
            ws_conn = None


async def message_handle(
    message: Message,
    group_id: str | None,
):
    time = str(datetime.now().replace(microsecond=0))
    messages = []
    # for m in message:
    #     if isinstance(m, str):
    #         messages.append(MessageItem(type="text", msg=str(m), time=time))
    #     elif isinstance(m, Image):
    #         if m.url:
    #             messages.append(MessageItem(type="img", msg=m.url, time=time))
    #     elif isinstance(m, At):
    #         if group_id:
    #             if m.target == "0":
    #                 uname = "全体成员"
    #             else:
    #                 uname = m.target
    #                 if group_id not in ID2NAME:
    #                     ID2NAME[group_id] = {}
    #                 if m.target in ID2NAME[group_id]:
    #                     uname = ID2NAME[group_id][m.target]
    #                 elif group_user := await GroupInfoUser.get_or_none(
    #                     user_id=m.target, group_id=group_id
    #                 ):
    #                     uname = group_user.user_name
    #                     if m.target not in ID2NAME[group_id]:
    #                         ID2NAME[group_id][m.target] = uname
    #             messages.append(MessageItem(type="at", msg=f"@{uname}", time=time))
    #     elif isinstance(m, Hyper):
    #         messages.append(MessageItem(type="text", msg="[分享消息]", time=time))
    return messages


# @matcher.handle()
# async def _(
#     message: Message, event: MessageEvent, session: Uninfo, uname: str = UserName()
# ):
#     global ws_conn, ID2NAME, ID_LIST
#     if ws_conn and ws_conn.client_state == WebSocketState.CONNECTED:
#         msg_id = event.message_id
#         if msg_id in ID_LIST:
#             return
#         ID_LIST.append(msg_id)
#         if len(ID_LIST) > 50:
#             ID_LIST = ID_LIST[40:]
#         gid = session.group.id if session.group else None
#         messages = await message_handle(message, gid)
#         data = Message(
#             object_id=gid or session.user.id,
#             user_id=session.user.id,
#             group_id=gid,
#             message=messages,
#             name=uname,
#             ava_url=AVA_URL.format(session.user.id),
#         )
#         await ws_conn.send_json(data.to_dict())
