from ATRI.utils.model import BaseModel


class LKImgLibConfig(BaseModel):
    """
    图库设置:
    """
    allow_global_store : bool = True
    allow_group_store : bool = True
    block_group_store : list = []
    global_lib_permission: list = []
    group_lib_permission: dict = {}
