from ATRI.configs import Field, PluginConfig


class LKImgLibConfig(PluginConfig):
    """
    图库设置:
    allow_global_store bool 是否启用全局图库相关指令
    allow_group_store bool 是否启用群聊图库相关指令
    block_group_store list[str] 阻止列表中的群聊使用指令
    global_lib_permission list[str] 为用户添加的全局图库权限
    group_lib_permission dict[str, list[str]] 为群聊的用户添加本群的群聊图库权限
    """

    allow_global_store = Field(bool, True, "启用全局图库")
    allow_group_store = Field(bool, True, "启用群聊图库")
    block_group_store = Field(list, [], "群聊图库禁用群", item_type=str)
    global_lib_permission = Field(list, [], "全局图库授权用户", item_type=str)
    group_lib_permission = Field(
        dict,
        {},
        "群聊图库授权用户",
        key_field=Field(str, "", "群号"),
        item_type=Field(list, [], "授权用户", item_type=Field(str, "", "用户 ID")),
    )
