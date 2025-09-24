from ATRI.utils.model import BaseModel


class LKImgLibConfig(BaseModel):
    """
    图库设置:
    allow_global_store bool 是否启用全局图库相关指令
    allow_group_store bool 是否启用群聊图库相关指令
    block_group_store list[str] 阻止列表中的群聊使用指令
    global_lib_permission list[str] 为用户添加的全局图库权限
    group_lib_permission dict[str: list[str]] 为群聊的用户添加本群的群聊图库权限
    """
    allow_global_store : bool = True
    allow_group_store : bool = True
    block_group_store : list = []
    global_lib_permission: list = []
    group_lib_permission: dict = {}
