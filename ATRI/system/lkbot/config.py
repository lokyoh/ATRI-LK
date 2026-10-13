from ATRI.configs import Field, PluginConfig


class LKConfig(PluginConfig):
    """
    LK插件设置
    """

    test_groups = Field(
        list, [], "测试模式群聊", "输入群号以参加测试功能", item_type=str
    )
    r18_groups = Field(
        list, [], "非健康模式群聊", "输入群号以接接触康模式限制", item_type=str
    )
    chat_switch = Field(bool, True, "聊天开关", "控制插件聊天部分是否启用")


config = LKConfig("LK插件")
"""lkbot插件设置"""
