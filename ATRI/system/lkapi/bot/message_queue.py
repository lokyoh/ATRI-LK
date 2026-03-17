from nonebot.message import event_preprocessor
from nonebot.adapters.onebot.v11 import MessageEvent, Bot, GroupMessageEvent, PrivateMessageEvent
from nonebot.adapters.onebot.v11.message import Message, MessageSegment

from ATRI.exceptions import str_traceback
from ATRI.log import log


class MessageQueueObject:
    def __init__(self, message: Message | MessageSegment | str, target_user_id: str | int, description: str,
                 source_plugin: str = None):
        self.msg = message
        self.uid = str(target_user_id)
        self.dec = description
        self.spg = source_plugin


class MessageQueue:
    def __init__(self):
        self.messages: dict[str, list[MessageQueueObject]] = {}

    def add_msg(self, msg: MessageQueueObject):
        if not msg.uid:
            raise ValueError("未添加用户")
        if not msg.uid in self.messages:
            self.messages[msg.uid] = []
        for m in self.messages[msg.uid]:
            if msg.dec == m.dec and msg.spg == m.spg and msg.uid == m.uid:
                m.msg = msg.msg
                log.debug(f"add {msg.dec} from {msg.spg} to {msg.uid}")
                return
        self.messages[msg.uid].append(msg)
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
        if uid in self.messages:
            for m in self.messages[uid]:
                if m.dec == dec and m.spg == source:
                    return True
        return False

    def remove_msg(self, uid: str | int, dec: str, source: str) -> bool:
        if uid in self.messages:
            for m in self.messages[uid]:
                if m.dec == dec and m.spg == source:
                    self.messages[uid].remove(m)
                    return True
        return False

    def remove_msgs(self, uid: str):
        if uid in self.messages:
            del (self.messages[uid])

    async def check_user(self, bot: Bot, event: MessageEvent) -> list[MessageQueueObject] | None:
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
