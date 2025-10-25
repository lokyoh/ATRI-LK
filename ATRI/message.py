import base64
from io import BytesIO
from pathlib import Path
from time import sleep
from typing import Optional, Union, Type

from nonebot.adapters.onebot.v11.message import Message, MessageSegment
from nonebot.internal.matcher import Matcher


class MessageBuilder(Message):
    """
    消息构建器，可当作消息发送。
    """

    def at(self, user_id: Union[int, str]) -> "MessageBuilder":
        """
        添加@类型消息。
        :param user_id: @对象
        :return: 消息构建器本身
        """
        self.append(MessageSegment.at(user_id))
        return self

    def face(self, id_: int) -> "MessageBuilder":
        """
        添加表情类型消息。
        :param id_: 表情id
        :return: 消息构建器本身
        """
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
        """
        添加图片类型消息。
        :param file: 图片文件
        :param type_: 类型
        :param cache: 是否启用缓存
        :param proxy: 是否启用代理
        :param timeout: 超时时间
        :return: 消息构建器本身
        """
        self.append(MessageSegment.image(file, type_, cache, proxy, timeout))
        return self

    def reply(self, id_: int) -> "MessageBuilder":
        """
        为消息添加回复。
        :param id_: 回复消息的id
        :return: 消息构建器本身
        """
        self.append(MessageSegment.reply(id_))
        return self

    def text(self, text: str) -> "MessageBuilder":
        """
        添加文本类型消息。
        :param text: 文本
        :return: 消息构建器本身
        """
        if len(self) != 0 and self[-1].type == "text":
            text = "\n" + text
        self.append(MessageSegment.text(text))
        return self

    def auto_append(self, message: MessageSegment | str) -> "MessageBuilder":
        if type(message) is str:
            self.text(message)
        else:
            self.append(message)
        return self

    def done(self) -> str:
        """
        转化为纯文本消息。
        :return: 纯文本消息
        """
        return str().join(map(str, self))


class MessageGroup:
    """
    消息组，用于储存消息并分条发送。
    """

    def __init__(self):
        self.message_list = []

    def add_message(self, message: str | MessageSegment | Message | MessageBuilder):
        """
        向消息组添加消息。
        :param message: 可被直接发送的消息
        :return: 消息组本身
        """
        self.message_list.append(message)
        return self

    async def send_message(self, matcher: Type[Matcher]):
        """
        使用指定匹配器逐条发送消息。
        :param matcher: 匹配器
        """
        for m in self.message_list:
            await matcher.send(m)
            sleep(1)


class PageMessage:
    """
    分页消息。
    """
    def __init__(self,
                 item_list: list,
                 header: str = f"标题\n{'-' * 20}\n",
                 footer: str = f"{'-' * 20}\n页数:{{page}} 共:{{i}}/{{num}}",
                 content: str = "{i:02d}.{item}",
                 page_num: int = 20
                 ):
        """
        分页消息。
        :param item_list: 需要分页的对象
        :param header: 分页标题
        :param footer: 页脚:其中page为当前页数,i为当前页总项目数,num为总项目数
        :param content: 每条的输出模板:其中i为编号,item为内容
        :param page_num: 每页大小
        """
        self._ml = MessageGroup()
        num = len(item_list)
        temp_msg = header
        i = 0
        page = 1
        for item in item_list:
            if i == page * page_num:
                self._ml.add_message(temp_msg + footer.format(page=page, i=i, num=num))
                temp_msg = ''
                page += 1
            i += 1
            temp_msg += content.format(i=i, item=item) + '\n'
        self._ml.add_message(temp_msg + footer.format(page=page, i=i, num=num))

    async def send_message(self, matcher: Type[Matcher]):
        """
        使用指定匹配器逐条发送消息。
        :param matcher: 匹配器
        """
        await self._ml.send_message(matcher)


def img_msg(file: str | bytes | BytesIO | Path) -> MessageSegment:
    """
    图片数据转图片消息。
    :param file: 图片数据
    :return: 可被直接发送的消息
    """
    return MessageSegment.image(file)


def img_msg_from_path(path: str | Path) -> MessageSegment:
    """
    图片路径转图片消息。
    :param path: 图片路径
    :return: 可被直接发送的消息
    """
    with open(path, "rb") as image_file:
        return img_msg(image_file.read())


def rec_msg(file: str | bytes | BytesIO | Path) -> MessageSegment:
    """
    语音数据转语音消息。
    :param file: 语音数据
    :return: 可被直接发送的消息
    """
    return MessageSegment.record(file)


def rec_msg_from_path(path: str | Path) -> MessageSegment:
    """
    语音路径转语音消息。
    :param path: 语音路径
    :return: 可被直接发送的消息
    """
    with open(path, "rb") as audio_file:
        audio_data = audio_file.read()
    base64_encoded_audio = base64.b64encode(audio_data).decode('utf-8')
    return rec_msg(f'base64://{base64_encoded_audio}')


def file_msg(name: str, data: bytes):
    """
    用于上传文件，只接受文件的bytes数据。
    :param name: 文件名
    :param data: 文件数据
    :return: 可被直接发送的消息
    """
    return MessageSegment(
        'file',
        {
            "name": name,
            "file": f'base64://{base64.b64encode(data).decode('utf-8')}'
        }
    )
