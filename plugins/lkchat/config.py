from ATRI.utils.model import BaseModel


class LKChatConfig(BaseModel):
    """
    lkchat设置:
    """
    model: str = 'gemini-main'
    max_history: int = 20
    max_text_length: int = 100
