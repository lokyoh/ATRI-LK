from ATRI.utils.model import BaseModel


class HelpConfig(BaseModel):
    """
    帮助设置:
    type: str 种类
    """
    help_type: str = 'text'
