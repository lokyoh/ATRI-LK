from nonebot.message import Matcher


class ChatSender:
    """发送通道。

    ``sent_count`` 记录本次链路**真的发出去了几条**。主动发言限流只认这个数——
    「决定要说」和「说出口了」是两件事，模型可能在思考后只调用
    ``stop_thinking`` 一句不说，那种情况不该占用发言配额。

    ``mtype`` 声明这个通道属于哪种消息协议，供 ``send_md_message`` 决定
    用图片还是 Markdown 卡片渲染：0 = OneBot（渲染成图片），1 = QQ。
    靠 isinstance 判断会漏掉新增的通道——``BotChatSender`` 就曾因此
    落到 else 分支，把 Markdown 源码直接发进群。
    """

    #: 0 = OneBot（md 渲染成图片），1 = QQ（发 Markdown 卡片）
    mtype = 0

    def __init__(self) -> None:
        self.sent_count = 0
        self.md_refs: list[tuple[str, str]] = []

    async def send(self, message):
        pass

    async def finish(self, message=None):
        pass


def bot_mtype(bot) -> int:
    """判断这个 bot 属于哪种协议：0 = OneBot，1 = QQ 官方。"""
    try:
        from nonebot.adapters.qq import Bot as QQBot

        if isinstance(bot, QQBot):
            return 1
    except Exception:
        pass
    return 0


async def send_to_chat(bot, chat_id, message) -> bool:
    """不经过 matcher，直接用 bot 往会话里发一条消息。

    主动开口与定时任务都没有 matcher（它们不是由用户消息触发的），
    只能走 bot 的原生 API。两种协议各有不同：

    - **OneBot**：``send_group_msg(group_id=...)``，群号是数字，
      非数字（QQ 官方 openid）原样传。
    - **QQ 官方**：``post_group_messages(group_openid=..., msg_type=0, content=...)``，
      用 openid 而不是数字群号。

    :return: 是否发送成功。
    """
    text = str(message or "").strip()
    if not text:
        return False

    try:
        if bot_mtype(bot) == 1:
            await bot.post_group_messages(
                group_openid=str(chat_id), msg_type=0, content=text
            )
            return True
        from .initiative_start import _group_id

        await bot.send_group_msg(group_id=_group_id(chat_id), message=text)
        return True
    except Exception as exc:
        from ATRI.log import log

        log.warning(f"发送到会话 {chat_id} 失败：{exc}")
        return False


class OneBotChatSender(ChatSender):
    def __init__(self, matcher: Matcher):
        super().__init__()
        self.matcher: Matcher = matcher

    async def send(self, message):
        await self.matcher.send(message)
        self.sent_count += 1

    async def finish(self, message=None):
        if message is not None:
            self.sent_count += 1
        await self.matcher.finish(message)


class QQChatSender(ChatSender):
    def __init__(self, matcher: Matcher):
        super().__init__()
        self.matcher: Matcher = matcher

    async def send(self, message):
        await self.matcher.send(message)
        self.sent_count += 1

    async def finish(self, message=None):
        if message is not None:
            self.sent_count += 1
        await self.matcher.finish(message)


class BotChatSender(ChatSender):
    """直接拿 bot 往群里发消息，不经过 matcher。

    定时任务到点时没有 matcher（它不是由用户消息触发的），
    但 Agent 的思考链路需要一个发送通道，所以这里直接用 bot。

    ``sent`` 记录真正发出去的文本，供调用方写历史——
    合成上下文不参与 ``add_response`` 回写，需要这个列表补记。

    ``cleaner`` 是可选的清洗函数。模型偶尔会把「话」和「理由」一起写出来、
    或带上 Markdown 与说话人前缀。主动开口是「先捕获、清洗、再发」，
    这里传同一个清洗函数即可获得同样保护；返回空串则不发送。

    ``finish`` 与 ``send`` 等价：没有 matcher 就没有「结束事件响应器」的概念。
    """

    def __init__(self, bot, chat_id, cleaner=None):
        super().__init__()
        self.bot = bot
        self.chat_id = str(chat_id)
        self.sent: list[str] = []
        self.cleaner = cleaner
        self.mtype = bot_mtype(bot)

    async def send(self, message):
        raw = message
        text = str(message or "").strip()
        if not text:
            return
        is_rich = text.startswith("[CQ:") or text.startswith("base64://")
        if self.cleaner is not None and not is_rich:
            text = str(self.cleaner(text) or "").strip()
            if not text:
                return
            raw = text
        if not await send_to_chat(self.bot, self.chat_id, raw):
            return
        self.sent_count += 1
        self.sent.append(text)

    async def finish(self, message=None):
        if message is not None:
            await self.send(message)
