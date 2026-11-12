from nonebot.adapters import Bot


class ChatArgs:
    def __init__(
        self,
        bot: Bot,
        chat_id: str,
        user_id: str,
        skip_chat: bool,
        skip_judgment: bool,
        level: str = "",
        group_id: str = "",
        is_private: bool = False,
        role: str = "",
    ):
        self.bot = bot
        self.chat_id = chat_id
        self.user_id = str(user_id)
        self.skip_chat = skip_chat
        self.skip_judgment = skip_judgment
        #: 显式等级。只有内部链路（定时任务 / system）会填；普通聊天留空，
        #: 由 ``PermissionContext.from_chat_args`` 在运行时按平台身份解析。
        self.level = str(level or "")
        self.group_id = str(group_id or "")
        self.is_private = bool(is_private)
        self.role = str(role or "")

    def is_force_chat(self):
        return (not self.skip_chat) and self.skip_judgment
