import hashlib
import io
import json
import os
from sqlite3 import Error as SQLiteError

from nonebot.adapters.onebot.v11 import Message
from nonebot.adapters.onebot.v11 import MessageSegment as OnebotMessageSegment
from nonebot.adapters.onebot.v11.event import Reply

from ATRI.log import log
from ATRI.utils.datetime import now, now_timestamp
from ATRI.utils.limiter import LimitedQueue
from ATRI.utils.model import BaseModel

from ..config import config
from ..llm.model import ModelRequestError
from .database import img_table
from .util import get_name


class ImageHistory:
    def __init__(self):
        self.images: dict[str, list] = {}
        self.index = 0

    async def add_image(self, mid, url, file_name, file_size=0):
        """添加图片并调用 IMAGE 模型进行描述"""
        from ATRI.log import log
        from ATRI.system.agent.utils import request

        from ..llm import ModelType, llm_manager
        from ..llm.contents import LLMContents

        if not llm_manager.has_type(ModelType.IMAGE):
            log.warning("没有配置image类型的模型")
            return "[图片]"

        text = None
        if file_name and file_size:
            cached_images = img_table.select(
                "DESP", {"FILE_NAME": file_name, "FILE_SIZE": file_size}
            )
            if cached_images and cached_images[0][0]:
                text = cached_images[0][0]
                log.info(f"使用图片描述缓存：{file_name}")

        # 下载图片
        if text is None:
            try:
                response = await request.get(url)
                image_bytes = response.content
                image_hash = hashlib.sha256(image_bytes).hexdigest()
                image_size = file_size or len(image_bytes)
                cached_images = img_table.select(
                    "DESP", {"HASH": image_hash, "FILE_SIZE": image_size}
                )
                if cached_images and cached_images[0][0]:
                    text = cached_images[0][0]
                    log.info(f"使用图片哈希描述缓存：{image_hash}")
                if text is not None:
                    image_bytes = None
                if text is None:
                    from PIL import Image

                    # 视觉模型的图片 Token 主要取决于分辨率，统一压缩比仅处理大文件更省 Token。
                    with Image.open(io.BytesIO(image_bytes)) as image:
                        image.thumbnail((1536, 1536), Image.Resampling.LANCZOS)
                        compressed_image = io.BytesIO()
                        image.convert("RGB").save(
                            compressed_image,
                            format="JPEG",
                            quality=75,
                            optimize=True,
                        )
                        image_bytes = compressed_image.getvalue()

                    # 转换为 base64
                    import base64

                    base64_str = base64.b64encode(image_bytes).decode("utf-8")
                    image_base64 = f"data:image/jpeg;base64,{base64_str}"
                    # 构建 LLMContents
                    contents = LLMContents()
                    contents.image(image_base64)
                    # 添加文本提示词
                    contents.text(
                        "请用中文描述这张图片的内容。如果有文字，请把文字描述概括出来，请留意其主题，直观感受，输出为一段平文本，请注意不要分点，就输出一段文本"
                    )
                    # 调用 IMAGE 模型
                    try:
                        result = await llm_manager.call_model_by_type(
                            ModelType.IMAGE, contents
                        )
                        text = result.content
                        if file_name and image_size and text:
                            try:
                                img_table.insert(
                                    {
                                        "FILE_NAME": file_name,
                                        "HASH": image_hash,
                                        "FILE_SIZE": image_size,
                                        "DESP": text,
                                        "UPDATE_AT": now_timestamp(),
                                    }
                                )
                            except SQLiteError as e:
                                log.warning(f"保存图片描述缓存失败：{e}")
                    except ModelRequestError:
                        text = "图片内容获取失败"
            except Exception as e:
                log.error(f"图片描述失败：{e}")
                text = "图片内容获取失败"
        log.info(f"获取图片描述：{text}")
        if mid is None:
            return f"[图片:{text}]", text
        if mid not in self.images:
            self.images[mid] = []
        self.images[mid].append(f"图片{self.index}: {text}")
        self.index += 1
        return f"[图片{self.index - 1}]", text

    def remove(self, mid):
        if mid in self.images:
            del self.images[mid]

    def get_history(self):
        return "\n".join(["\n".join(v) for v in self.images.values()]) or ""

    def get_desc(self, mid):
        return self.images.get(mid, None)


class MessageSegment:
    def __init__(self, msg):
        self.data = msg

    async def get_msg(self, bot):
        return self.data

    def get_msg_str(self):
        return self.data


class AtMessageSegment(MessageSegment):
    def __init__(self, target):
        super().__init__(target)

    async def get_msg(self, bot):
        return f"[@{await get_name(bot, self.data[0], self.data[1])} id:{self.data[0]}]"

    async def get_msg_str(self):
        return f"[@{self.data[0]}]"


class ImageMessageSegment(MessageSegment):
    def __init__(self, msg, desp):
        super().__init__({"msg": msg, "desp": desp})

    async def get_msg(self, bot):
        return self.data["msg"]

    def get_msg_str(self):
        return f"[图片:{self.data['desp']}]"


class LLMMessage:
    def __init__(self, group_id, message: str | None = None, reply: str = ""):
        self.message = [] if message is None else message
        self.group_id = group_id
        self.reply = reply

    @classmethod
    async def create(cls, self_id, message: Message, mid, group_id):
        instance = cls(group_id)
        if group_id not in img_history:
            img_history[group_id] = ImageHistory()
        img_count = 0
        for segment in message:
            if segment.type == "text":
                instance.message.append(MessageSegment(segment.data["text"]))
            elif segment.type == "at":
                instance.message.append(
                    AtMessageSegment((segment.data["qq"], group_id))
                )
            elif segment.type == "face":
                face_text = segment.data.get("raw", {}).get("faceText", "")
                if face_text:
                    face_text = face_text.replace("[", "[表情:", 1)
                    instance.message.append(MessageSegment(face_text))
            elif segment.type == "image":
                if img_count >= 3:
                    log.warning("图片数量过多，已忽略")
                    continue
                file_name = segment.data.get("file", "")
                url = segment.data.get("url", "")
                file_size = segment.data.get("file_size", 0)
                if url:
                    result, desp = await img_history[group_id].add_image(
                        mid, url, file_name, file_size
                    )
                    instance.message.append(ImageMessageSegment(result, desp))
                img_count += 1
            elif segment.type == "video":
                instance.message.append(MessageSegment("[视频]"))
            elif segment.type == "file":
                continue
            elif segment.type == "json":
                now_type = "json"
                try:
                    json_data = json.loads(segment.data["data"])
                    if "detail_1" in json_data["meta"]:
                        json_meta = json_data["meta"]["detail_1"]
                        if json_meta["title"] == "哔哩哔哩":
                            now_type = "哔哩哔哩"
                            title = json_meta["title"]
                            instance.message.append(f"[B站分享:{title}]")
                            continue
                    log.info("存在未支持的json消息类型")
                    log.debug(f"消息内容：{segment.data}")
                except Exception as e:
                    log.warning(f"{now_type}消息处理错误：{e}")
                    log.debug(f"消息内容：{segment.data}")
            elif segment.type == "forward":
                content = segment.data.get("content", [])
                if not content:
                    continue
                msg_his = []
                for msg in content:
                    msg_type = msg.get("message_format", "")
                    if msg_type in ("array"):
                        try:
                            sender = msg["sender"]
                            f_msg = msg["message"]
                            f_msg_obj = Message()
                            for f_msg_seg in f_msg:
                                f_msg_obj.append(
                                    OnebotMessageSegment(
                                        f_msg_seg["type"], f_msg_seg["data"]
                                    )
                                )
                            temp_message = await LLMTempMessage.create(
                                self_id, f_msg_obj, group_id, sender
                            )
                            msg_his.append(temp_message.get_message_str())
                        except Exception as e:
                            log.warning(f"转发消息处理错误：{e}")
                            log.debug(f"消息内容：{msg}")
                    else:
                        log.info(f"转发存在未支持的类型{msg_type}")
                if msg_his:
                    from ..llm import ModelType, llm_manager

                    forward_history = "\n".join(msg_his)
                    if llm_manager.has_type(ModelType.TOOL):
                        try:
                            summary_prompt = (
                                "请总结以下转发消息，保留主要人物、事件和结论，"
                                "输出一段简洁的中文纯文本，不要分点：\n"
                                f"{forward_history}"
                            )
                            summary = await llm_manager.call_model_by_type(
                                ModelType.TOOL, summary_prompt
                            )
                            instance.message.append(
                                MessageSegment(f"[转发消息摘要:{summary.content}]")
                            )
                        except ModelRequestError as e:
                            log.warning(f"转发消息总结失败：{e}")
                            instance.message.append(MessageSegment("[转发消息]"))
                    else:
                        instance.message.append(MessageSegment("[转发消息]"))
            elif segment.type == "atri_reply" or segment.type == "reply":
                reply_msg = None
                try:
                    if segment.type == "reply":
                        # 传统的reply需要获取原消息，搁置
                        # f_msg = segment.data["message"]
                        # f_msg_obj = Message()
                        # for f_msg_seg in f_msg:
                        #     f_msg_obj.append(
                        #         OnebotMessageSegment(
                        #             f_msg_seg["type"], f_msg_seg["data"]
                        #         )
                        #     )
                        # reply_message = f_msg_obj
                        # reply_sender = segment.data["sender"]
                        continue
                    else:
                        reply_data: Reply = segment.data
                        reply_message = reply_data.message
                        reply_sender = {
                            "user_id": reply_data.sender.user_id,
                            "nickname": reply_data.sender.nickname,
                        }
                    reply_msg: LLMTempMessage = await LLMTempMessage.create(
                        self_id,
                        reply_message,
                        group_id,
                        reply_sender,
                    )
                except Exception as e:
                    log.warning(f"回复消息处理错误：{e}")
                    log.debug(f"消息内容：{segment.data}")
                if reply_msg is not None:
                    r_s = f"{reply_msg.nickname} id:{reply_msg.user_id}"
                    if str(reply_msg.self_id) == reply_msg.user_id:
                        r_s = "你"
                    instance.reply = f"[回复 {r_s} [{reply_msg.get_message_str()}]]"
            else:
                log.info(f"未支持的消息类型：{segment.type}")
                log.debug(f"消息内容：{segment.data}")
        return instance

    async def get_message(self, bot) -> str:
        msgs = [await m.get_msg(bot) for m in self.message]
        str_msg = "".join(msgs)
        if self.reply:
            str_msg = self.reply + str_msg
        return str_msg

    def get_message_str(self) -> str:
        msgs = [m.get_msg_str() for m in self.message]
        str_msg = "".join(msgs)
        if self.reply:
            str_msg = self.reply + str_msg
        return str_msg


class LLMTempMessage(LLMMessage):
    def __init__(self, msg: LLMMessage, sender: dict, self_id: str):
        super().__init__(msg.group_id, msg.message, msg.reply)
        self.user_id = str(sender["user_id"])
        self.nickname = sender["nickname"]
        self.self_id = self_id

    @classmethod
    async def create(
        cls, self_id, message: Message, group_id, sender
    ) -> "LLMTempMessage | None":
        msg = await LLMMessage.create(self_id, message, None, group_id)
        if msg is None:
            return None
        return cls(msg, sender, self_id)

    async def get_message(self, bot) -> str:
        raise RuntimeError("不支持获取动态信息")

    def get_message_str(self) -> str:
        r_s = f"{self.nickname}[id:{self.user_id}]"
        if str(self.self_id) == self.user_id:
            r_s = "你发送了"
        return f"{r_s}: {super().get_message_str()}"


class History:
    now_m_id = 0

    def __init__(self, sender, group_id):
        self.mid = History.now_m_id
        History.now_m_id += 1
        self.sender = sender
        self.group_id = group_id
        self.time = now().strftime("%Y年%m月%d日%a-%H时%M分")
        self.message: LLMMessage | None = None
        self.response = ""

    @classmethod
    async def create(cls, self_id, sender, group_id, message):
        instance = cls(sender, group_id)
        instance.message = await LLMMessage.create(
            self_id, message, instance.mid, group_id
        )
        if instance.message is None or len(instance.message.message) == 0:
            return None
        return instance

    async def get_message(self, bot, get_reply=True):
        msg = f"mid:{self.mid} {self.time} {await get_name(bot, self.sender, self.group_id)}[id:{self.sender}]: {await self.message.get_message(bot)}"
        if self.response and get_reply:
            msg += "\n你对此回复了:" + self.response
        return msg

    def add_response(self, response):
        self.response += f" {response}"
        history_logger.add_history(None, self.group_id, self.response)


class HistoryNode(BaseModel):
    sender: int | None
    time: str
    message: str


class HistoryModel(BaseModel):
    count: int = 0
    group_id: str
    history: list[HistoryNode]


class HistoryLogger:
    def __init__(self):
        from ..service import plugin

        self.path = plugin.get_path() / "group"
        if not self.path.exists():
            self.path.mkdir(parents=True, exist_ok=True)

    def get_today_date(self):
        return now().strftime("%Y-%m-%d")

    def add_history(self, user_id, group_id, history: History | str):
        file_path = self.path / str(group_id) / f"{self.get_today_date()}.json"
        if not file_path.parent.exists():
            file_path.parent.mkdir(parents=True, exist_ok=True)
        if not os.path.exists(file_path):
            history_model = HistoryModel(group_id=group_id, history=[])
        else:
            history_model = HistoryModel.read_from_file(file_path)
        h_time = now().strftime("%H:%M")
        if user_id is None:
            history_model.history.append(
                HistoryNode(sender=None, time=h_time, message=history)
            )
        else:
            history_model.history.append(
                HistoryNode(
                    sender=user_id,
                    time=h_time,
                    message=history.message.get_message_str(),
                )
            )
        history_model.count += 1
        history_model.write_into_file(file_path)

    def get_history(self, group_id, date: str | None = None):
        if date is None:
            date = self.get_today_date()
        file_path = self.path / str(group_id) / f"{date}.json"
        if not os.path.exists(file_path):
            return None
        history_model = HistoryModel.read_from_file(file_path)
        return history_model


class ChatHistory:
    def __init__(self):
        self.history = LimitedQueue(config.max_history)

    async def add_history(self, self_id, user_id, group_id, message: Message | History):
        if isinstance(message, Message):
            h = await History.create(self_id, user_id, group_id, message)
            if h is None:
                return None
        else:
            h = message
        history_logger.add_history(user_id, group_id, h)
        if h := self.history.add(h):
            if group_id not in img_history:
                img_history[group_id] = ImageHistory()
            else:
                img_history[group_id].remove(h.mid)
        return h

    def get_history(self) -> list[History]:
        return self.history.get_data()

    def get_last_history(self) -> History:
        return self.history.get_data()[-1]

    def add_reply(self, reply: str):
        self.get_last_history().add_response(reply)


history_logger = HistoryLogger()
chat_history: dict[str, ChatHistory] = {}
img_history: dict[str, ImageHistory] = {}
