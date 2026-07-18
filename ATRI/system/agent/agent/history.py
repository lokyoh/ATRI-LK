from datetime import datetime

from nonebot.adapters.onebot.v11 import Message

from ATRI.log import log
from ATRI.utils.limiter import LimitedQueue

from ..config import config
from .util import get_name


class ImageHistory:
    def __init__(self):
        self.images: dict[str, list] = {}
        self.index = 0

    async def add_image(self, mid, url):
        """添加图片并调用 IMAGE 模型进行描述"""
        from ATRI.log import log
        from ATRI.system.agent.utils import request

        from ..llm import ModelType, llm_manager
        from ..llm.contents import LLMContent, LLMContents

        if not llm_manager.has_type(ModelType.IMAGE):
            log.warning("没有配置image类型的模型")
            return "[图片]"
        # 下载图片
        try:
            response = await request.get(url)
            image_bytes = response.content
            # 转换为 base64
            import base64

            base64_str = base64.b64encode(image_bytes).decode("utf-8")
            image_base64 = f"data:image/jpeg;base64,{base64_str}"
            # 构建 LLMContents
            contents = LLMContents()
            img_content = LLMContent("image", image_base64)
            contents.add_content(img_content)
            # 添加文本提示词
            text_content = LLMContent(
                "text",
                "请用中文描述这张图片的内容。如果有文字，请把文字描述概括出来，请留意其主题，直观感受，输出为一段平文本，请注意不要分点，就输出一段文本",
            )
            contents.add_content(text_content)
            # 调用 IMAGE 模型
            result = await llm_manager.call_model_by_type(ModelType.IMAGE, contents)
            if isinstance(result, dict):
                text = result.get("content", "图片内容获取失败")
            else:
                text = str(result) if result else "图片内容获取失败"
        except Exception as e:
            from ATRI.log import log

            log.error(f"图片描述失败：{e}")
            text = "图片内容获取失败"
        log.info(f"获取图片描述：{text}")
        if mid not in img_history:
            self.images[mid] = []
        self.images[mid].append(f"图片{self.index}: {text}")
        self.index += 1
        return f"[图片{self.index}]"

    def remove(self, mid):
        if mid in self.images:
            del self.images[mid]

    def get_history(self):
        return "\n".join(["\n".join(v) for v in self.images.values()]) or ""

    def get_desc(self, mid):
        return self.images.get(mid)


class MessageSegment:
    def __init__(self, msg):
        self.data = msg

    async def get_msg(self, bot):
        return self.data


class AtMessageSegment(MessageSegment):
    def __init__(self, target):
        super().__init__(target)

    async def get_msg(self, bot):
        return f"[@{await get_name(bot, self.data[0], self.data[1])} id:{self.data[0]}]"


class LLMMessage:
    def __init__(self, group_id):
        self.message = []
        self.group_id = group_id

    @classmethod
    async def create(cls, message: Message, mid, group_id):
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
                    log.warning(f"图片数量过多，已忽略：{img_count}")
                    continue
                url = segment.data.get("url", "")
                if url:
                    result = await img_history[group_id].add_image(mid, url)
                    instance.message.append(MessageSegment(result))
                img_count += 1
            elif segment.type == "video":
                instance.message.append(MessageSegment("[视频]"))
            elif segment.type == "file":
                pass
            else:
                log.warning(f"未支持的消息类型：{segment.type}\n{segment.data}")
        return instance

    async def get_message(self, bot) -> str:
        msgs = [await m.get_msg(bot) for m in self.message]
        return "".join(msgs)


class History:
    now_m_id = 0

    def __init__(self, sender, group_id):
        self.mid = History.now_m_id
        History.now_m_id += 1
        self.sender = sender
        self.group_id = group_id
        self.time = datetime.now().strftime("%Y年%m月%d日%a-%H时%M分")
        self.message = None
        self.response = ""

    @classmethod
    async def create(cls, sender, group_id, message):
        instance = cls(sender, group_id)
        instance.message = await LLMMessage.create(message, instance.mid, group_id)
        if instance.message is None:
            return None
        return instance

    async def get_message(self, bot, get_reply=True):
        msg = f"mid:{self.mid} {self.time} {await get_name(bot, self.sender, self.group_id)}[id:{self.sender}]: {await self.message.get_message(bot)}"
        if self.response and get_reply:
            msg += "\n你对此回复了:" + self.response
        return msg

    def add_response(self, response):
        self.response += f" {response}"


class ChatHistory:
    def __init__(self):
        self.history = LimitedQueue(config.max_history)

    async def add_history(self, user_id, group_id, message: Message | History):
        if isinstance(message, Message):
            h = await History.create(user_id, group_id, message)
            if h is None:
                return None
        else:
            h = message
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


chat_history: dict[str, ChatHistory] = {}
img_history: dict[str, ImageHistory] = {}
