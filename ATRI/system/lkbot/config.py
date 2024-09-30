from pathlib import Path

from ATRI.utils.model import BaseModel
from ATRI.configs import PluginConfig

DATA_PATH = Path(".") / "data" / "plugins" / "lkbot" / "config.json"


class Config(BaseModel):
    """
    LK插件设置:
    test_groups: list[str] 测试模式群聊
    r18_groups: list[str] 非健康模式群聊
    chat_switch: bool 聊天开关
    api_key: str 谷歌AI的api_key
    """
    test_groups: list[str] = []
    r18_groups: list[str] = []
    chat_switch: bool = True
    api_key: str = ''


_config_manage = PluginConfig("lk插件", Config)
config = _config_manage.config()


def load_config():
    """加载配置"""
    global config
    config = _config_manage.config()


def save_config():
    """保存设置"""
    _config_manage.change_config(config)
