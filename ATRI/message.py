import base64
from io import BytesIO
from pathlib import Path
from time import sleep
from typing import Optional, Union, Type

from nonebot.adapters.onebot.v11.message import Message, MessageSegment
from nonebot.internal.matcher import Matcher


class MessageBuilder(Message):
    def at(self, user_id: Union[int, str]) -> "MessageBuilder":
        self.append(MessageSegment.at(user_id))
        return self

    def face(self, id_: int) -> "MessageBuilder":
        self.append(MessageSegment.face(id_))
        return self

    def image(
            self,
            file: Union[str, bytes, BytesIO, Path],
            type_: Optional[str] = None,
            cache: bool = True,
            proxy: bool = True,
            timeout: Optional[int] = None,
    ) -> "MessageBuilder":
        self.append(MessageSegment.image(file, type_, cache, proxy, timeout))
        return self

    def reply(self, id_: int) -> "MessageBuilder":
        self.append(MessageSegment.reply(id_))
        return self

    def text(self, text: str) -> "MessageBuilder":
        if len(self) != 0 and self[-1].type == "text":
            text = "\n" + text
        self.append(MessageSegment.text(text))
        return self

    def done(self) -> str:
        return str().join(map(str, self))


class MessageGroup:
    """消息组:用于储存消息并分条发送"""

    def __init__(self):
        self.message_list = []

    def add_message(self, message: str | MessageSegment | Message):
        """向消息组添加消息"""
        self.message_list.append(message)
        return self

    async def send_message(self, matcher: Type[Matcher]):
        """使用指定匹配器逐条发送消息"""
        for m in self.message_list:
            await matcher.send(m)
            sleep(1)


def img_msg(file: str | bytes | BytesIO | Path) -> MessageSegment:
    return MessageSegment.image(file)


def rec_msg(file: str | bytes | BytesIO | Path) -> MessageSegment:
    return MessageSegment.record(file)


def file_msg(name: str, data: bytes):
    """用于上传文件，只接受文件的bytes数据"""
    return MessageSegment(
        'file',
        {
            "name": name,
            "file": f'base64://{base64.b64encode(data).decode('utf-8')}'
        }
    )
