from ATRI.utils.model import BaseModel


class HelpConfig(BaseModel):
    """
    帮助设置:
    help_type: str 帮助展示的种类
    """
    help_type: str = 'text'
