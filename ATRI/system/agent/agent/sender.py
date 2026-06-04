from nonebot.message import Matcher

class ChatSender:
    async def send(self, message):
        pass

class QQChatSender(ChatSender):
    def __init__(self, matcher: Matcher):
        self.matcher: Matcher = matcher

    async def send(self, message):
        await self.matcher.send(message)

    async def finish(self, message = None):
        await self.matcher.finish(message)
