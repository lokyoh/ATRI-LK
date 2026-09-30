import json
import re
from hashlib import sha256
from pathlib import Path

from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    MessageEvent,
    PrivateMessageEvent,
)
from nonebot.adapters.onebot.v11.message import Message, MessageSegment
from nonebot.message import event_preprocessor

from ATRI.dir import PLUGIN_DATA_DIR
from ATRI.exceptions import str_traceback
from ATRI.log import log

MQ_DIR: Path = PLUGIN_DATA_DIR / "lkbot" / "mq"
"""待发消息的本地存储目录：``data/plugins/lkbot/mq/``，每个用户一个 JSON 文件。"""

_UNSAFE_NAME = re.compile(r"[^0-9A-Za-z_-]")


def _file_name(uid: str) -> str:
    """把用户 ID 变成安全的文件名。

    正常情况（QQ 号）原样使用，便于人工排查；一旦需要替换字符，就再拼一段原文摘要，
    避免 ``a/b`` 和 ``a_b`` 这类不同 ID 落到同一个文件上。
    """
    safe = _UNSAFE_NAME.sub("_", uid)
    if safe != uid:
        safe = f"{safe}-{sha256(uid.encode('utf-8')).hexdigest()[:8]}"
    return f"{safe}.json"


def _encode_msg(msg: Message | MessageSegment | str) -> dict:
    """把消息序列化成 JSON 可存的结构。

    ``Message`` 是 ``list`` 的子类、``MessageSegment`` 并不是 ``dict`` 的子类，
    所以要显式拆出 ``type`` / ``data`` 两个字段。
    """
    if isinstance(msg, str):
        return {"kind": "str", "value": msg}
    if isinstance(msg, Message):
        return {
            "kind": "message",
            "value": [{"type": s.type, "data": dict(s.data)} for s in msg],
        }
    if isinstance(msg, MessageSegment):
        return {"kind": "segment", "value": {"type": msg.type, "data": dict(msg.data)}}
    return {"kind": "str", "value": str(msg)}


def _decode_msg(payload: dict) -> Message | MessageSegment | str:
    """``_encode_msg`` 的逆操作，未知结构退化成字符串。"""
    kind = payload.get("kind")
    value = payload.get("value")
    if kind == "message":
        return Message(
            [
                MessageSegment(type=s["type"], data=dict(s.get("data") or {}))
                for s in value or []
            ]
        )
    if kind == "segment":
        return MessageSegment(type=value["type"], data=dict(value.get("data") or {}))
    return value if isinstance(value, str) else str(value)


class MessageQueueObject:
    def __init__(
        self,
        message: Message | MessageSegment | str,
        target_user_id: str | int,
        description: str,
        source_plugin: str = None,
    ):
        self.msg = message
        self.uid = str(target_user_id)
        self.dec = description
        self.spg = source_plugin


class MessageQueue:
    """用户不在时先攒着的通知，等他下次说话再一并发出。

    内容会落到 :data:`MQ_DIR`，每个用户一个 JSON 文件，因此**重启不会丢待发消息**。
    写盘失败只记日志，绝不影响消息处理本身。
    """

    def __init__(self, storage_dir: Path | None = None):
        self.messages: dict[str, list[MessageQueueObject]] = {}
        self.storage_dir: Path = storage_dir if storage_dir is not None else MQ_DIR
        self.load()

    # ------------------------------------------------------------------ 本地存储

    def _path(self, uid: str) -> Path:
        return self.storage_dir / _file_name(str(uid))

    def _persist(self, uid: str) -> None:
        """把某个用户的待发消息写盘；队列为空时删除对应文件。"""
        uid = str(uid)
        try:
            path = self._path(uid)
            pending = self.messages.get(uid)
            if not pending:
                path.unlink(missing_ok=True)
                return
            self.storage_dir.mkdir(parents=True, exist_ok=True)
            payload = [
                {"msg": _encode_msg(m.msg), "uid": m.uid, "dec": m.dec, "spg": m.spg}
                for m in pending
            ]
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            log.error(f"保存消息队列失败({uid}): {str_traceback(e)}")

    def load(self) -> None:
        """启动时把磁盘上的待发消息读回内存。"""
        if not self.storage_dir.is_dir():
            return

        restored = 0
        for path in sorted(self.storage_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception as e:
                log.error(f"读取消息队列失败({path.name}): {str_traceback(e)}")
                continue
            if not isinstance(payload, list):
                log.error(f"消息队列格式错误({path.name}): 顶层应为列表")
                continue
            for item in payload:
                try:
                    obj = MessageQueueObject(
                        _decode_msg(item["msg"]),
                        item["uid"],
                        item.get("dec", ""),
                        item.get("spg"),
                    )
                except Exception as e:
                    log.error(f"解析消息队列条目失败({path.name}): {str_traceback(e)}")
                    continue
                if not obj.uid:
                    continue
                self.messages.setdefault(obj.uid, []).append(obj)
                restored += 1

        if restored:
            log.info(f"已从 {self.storage_dir} 恢复 {restored} 条待发消息")

    def save(self) -> None:
        """把内存里的全部待发消息写盘（一般不需要手动调用）。"""
        for uid in list(self.messages):
            self._persist(uid)

    # ------------------------------------------------------------------ 队列操作

    def add_msg(self, msg: MessageQueueObject):
        if not msg.uid:
            raise ValueError("未添加用户")
        if not msg.uid in self.messages:
            self.messages[msg.uid] = []
        for m in self.messages[msg.uid]:
            if msg.dec == m.dec and msg.spg == m.spg and msg.uid == m.uid:
                m.msg = msg.msg
                self._persist(msg.uid)
                log.debug(f"add {msg.dec} from {msg.spg} to {msg.uid}")
                return
        self.messages[msg.uid].append(msg)
        self._persist(msg.uid)
        log.debug(f"add {msg.dec} from {msg.spg} to {msg.uid}")

    def get_msg(self, uid: str | int) -> list[MessageQueueObject] | None:
        uid = str(uid)
        if uid in self.messages:
            return self.messages[uid]
        return None

    def _get_explain_msg(self, uid: str | int) -> list | None:
        msg_list = []
        for m in self.get_msg(uid):
            msg = m.msg
            msg_list.append(msg)
        return msg_list

    def has_msg(self, uid: str | int, dec: str, source: str) -> bool:
        uid = str(uid)
        if uid in self.messages:
            for m in self.messages[uid]:
                if m.dec == dec and m.spg == source:
                    return True
        return False

    def remove_msg(self, uid: str | int, dec: str, source: str) -> bool:
        uid = str(uid)
        if uid in self.messages:
            for m in self.messages[uid]:
                if m.dec == dec and m.spg == source:
                    self.messages[uid].remove(m)
                    self._persist(uid)
                    return True
        return False

    def remove_msgs(self, uid: str):
        uid = str(uid)
        if uid in self.messages:
            del self.messages[uid]
        self._persist(uid)

    async def check_user(
        self, bot: Bot, event: MessageEvent
    ) -> list[MessageQueueObject] | None:
        uid = event.get_user_id()
        log.debug(f"checking {uid} mq")
        if uid not in self.messages:
            log.debug(f"{uid} mq done")
            return
        msg_list = self._get_explain_msg(uid)
        self.remove_msgs(uid)
        for msg in msg_list:
            try:
                if type(event) is GroupMessageEvent:
                    event: GroupMessageEvent
                    await bot.send_group_msg(group_id=event.group_id, message=msg)
                elif type(event) is PrivateMessageEvent:
                    await bot.send_private_msg(user_id=event.user_id, message=msg)
                else:
                    log.warning(f"unsupported event {type(event)}")
            except Exception as e:
                log.error(str_traceback(e))
        log.debug(f"{uid} mq done")


msg_queue = MessageQueue()


@event_preprocessor
async def check_message_queue(bot: Bot, event: MessageEvent):
    await msg_queue.check_user(bot, event)
