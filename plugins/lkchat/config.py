from ATRI.utils.model import BaseModel


class LKChatConfig(BaseModel):
    """
    lkchat设置:
    """
    chat_switch: bool = False
    proactively_chat: bool = False
    key_word: list[str] = ["ATRI", "atri", "亚托莉", "Atri", "萝卜子"]
    whit_list: list[str] = []
