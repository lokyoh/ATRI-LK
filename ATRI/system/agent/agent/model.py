from nonebot.adapters.onebot.v11 import Bot


class ChatArgs:
    def __init__(
        self, bot: Bot, chat_id: str, user_id: str, skip_chat: bool, skip_judgment: bool
    ):
        self.bot = bot
        self.chat_id = chat_id
        self.user_id = user_id
        self.skip_chat = skip_chat
        self.skip_judgment = skip_judgment

    def is_force_chat(self):
        return (not self.skip_chat) and self.skip_judgment
