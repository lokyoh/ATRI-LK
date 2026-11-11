from ATRI.configs import Field, PluginConfig


class SignInConfig(PluginConfig):
    """
    signin设置:
    """

    base_source = Field(str, "local", "默认图片源")
    unique_source = Field(str, "lolicon", "特殊图片源")
