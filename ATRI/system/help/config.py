from ATRI.configs import Field, PluginConfig


class HelpConfig(PluginConfig):
    """
    帮助设置:
    help_type: str 帮助展示的种类
    """

    help_type = Field(
        str,
        "html",
        "帮助展示形式",
        options=[
            {"label": "HTML", "value": "html"},
            {"label": "图片", "value": "image"},
            {"label": "文本", "value": "text"},
        ],
    )
