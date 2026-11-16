from ATRI.configs import Field, PluginConfig


class LKChatConfig(PluginConfig):
    """
    lkchat设置:
    """

    chat_switch = Field(bool, False, "Agent聊天开关", "Agent聊天总开关,需和白名单配合")
    proactively_chat = Field(
        bool, False, "主动聊天", "当当前聊天符合规则时主动回复,需和白名单配合"
    )
    whit_list = Field(
        list, [], "聊天白名单", "只有在白名单的群才开启Agent聊天", item_type=str
    )
    proactively_chat_whit_list = Field(
        list, [], "主动聊天白名单", "只有在白名单的群才开启主动聊天", item_type=str
    )
