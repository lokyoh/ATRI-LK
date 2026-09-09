from pydantic import Field

from ATRI.configs import PluginConfig
from ATRI.utils.model import BaseModel


class Config(BaseModel):
    """
    LK插件设置:
    test_groups: list[str] 测试模式群聊
    r18_groups: list[str] 非健康模式群聊
    chat_switch: bool 聊天开关
    """
    test_groups: list[str] = Field(default_factory=list)
    r18_groups: list[str] = Field(default_factory=list)
    chat_switch: bool = True


_config_manage = PluginConfig("lk插件", Config)
config: Config = _config_manage.config()
"""lkbot插件设置"""


def load_config():
    """加载lkbot插件配置"""
    _config_manage.load_config()


def save_config():
    """保存lkbot插件设置"""
    _config_manage.change_config()
