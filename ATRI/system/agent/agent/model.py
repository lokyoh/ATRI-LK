from nonebot.adapters import Bot


def resolve_identity(user_id: str) -> str:
    """把原始账号 ID 解析成主账号 ID。

    统一入口，避免「有的地方解析了、有的地方没解析」。
    解析失败（无 lkbot 用户系统 / 绑定表异常）时退回原 ID：
    记忆功能不该因为身份系统不可用而整体失效。
    """
    raw = str(user_id)
    try:
        from ATRI.system.lkapi.entity.user import user_manager

        return user_manager.resolve_user_id(raw)
    except Exception:
        return raw


class ChatArgs:
    def __init__(
        self, bot: Bot, chat_id: str, user_id: str, skip_chat: bool, skip_judgment: bool
    ):
        self.bot = bot
        self.chat_id = chat_id
        self.user_id = resolve_identity(user_id)
        self.raw_user_id = str(user_id)
        """原始账号 ID，仅用于展示/日志；逻辑判断一律用 user_id。"""
        self.skip_chat = skip_chat
        self.skip_judgment = skip_judgment

    def is_force_chat(self):
        return (not self.skip_chat) and self.skip_judgment
