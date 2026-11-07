from ATRI.configs import Field, PluginConfig

OFFICIAL_BRANCH = "main"
"""正式版分支。"""

BETA_BRANCH = "dev"
"""测试版分支。"""


class UpdateConfig(PluginConfig):
    """
    更新设置:
    channel: str 更新通道，决定从哪个分支更新
    """

    channel = Field(
        str,
        OFFICIAL_BRANCH,
        "更新通道",
        "正式版从 main 分支更新，测试版从 dev 分支更新到最新提交",
        options=[
            {"label": "正式版（main）", "value": OFFICIAL_BRANCH},
            {"label": "测试版（dev）", "value": BETA_BRANCH},
        ],
    )

    @property
    def branch(self) -> str:
        """当前通道对应的分支名；未知取值回落到正式版。"""
        value = str(self.get("channel") or "").strip()
        return value if value in (OFFICIAL_BRANCH, BETA_BRANCH) else OFFICIAL_BRANCH

    @property
    def is_beta(self) -> bool:
        return self.branch == BETA_BRANCH
