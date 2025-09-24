from ATRI.utils.model import BaseModel


class SignInConfig(BaseModel):
    """
    signin设置:
    """
    base_source: str = 'local'
    unique_source: str = 'lolicon'
