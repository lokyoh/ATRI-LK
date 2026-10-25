from nonebot.message import Matcher


class ChatSender:
    """发送通道。

    ``sent_count`` 记录本次链路**真的发出去了几条**。主动发言限流只认这个数——
    「决定要说」和「说出口了」是两件事，模型可能在思考后只调用
    ``stop_thinking`` 一句不说，那种情况不该占用发言配额。
    """

    def __init__(self) -> None:
        self.sent_count = 0

    async def send(self, message):
        pass

    async def finish(self, message=None):
        pass


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
