from pathlib import Path
import os

from ATRI.utils.model import BaseModel

DATA_PATH = Path(".") / "data" / "plugins" / "lkbot" / "config.json"


class Config(BaseModel):
    version: int = 0
    android_client: bool = False
    test_groups: list[str] = []
    r18_groups: list[str] = []
    chat_switch: bool = True
    api_key: str = ''


class ConfigurationManager:
    """
    LK插件设置:
    test_groups: list[str] 测试模式群聊
    r18_groups: list[str] 非健康模式群聊
    chat_switch: bool 聊天开关
    api_key: str 谷歌AI的api_key
    """
    version = 0

    def __init__(self):
        self.load()

    def load(self):
        if os.path.exists(DATA_PATH):
            self.config = Config.read_from_file(DATA_PATH)
        else:
            self.config = Config(
                version=self.version,
                android_client=False,
                test_groups=[],
                r18_groups=[],
                chat_switch=True,
                api_key=''
            )
            self.save_config()
        if self.config.version < self.version:
            self.config.version = self.version
            self.save_config()

    def save_config(self):
        """保存设置"""
        os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
        self.config.write_into_file(DATA_PATH)


config = ConfigurationManager()
