from pathlib import Path
from typing import Type
import os

from ATRI.utils.model import BaseModel

plugin_config = {}
"""插件设置数据"""
CONFIG_DIR = Path(".") / "data" / "config"
"""插件设置路径"""


class PluginConfig:
    def __init__(self, service: str, model: Type[BaseModel]):
        """
        插件设置。
        :param service: 服务名
        :param model: 插件设置模型BaseModel
        """
        self.model = model
        self.path = CONFIG_DIR / f"{service}_config.json"
        if not os.path.exists(self.path):
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            self.change_config(self.model())
        self._config = self.load_config()
        plugin_config[service] = self

    def load_config(self):
        """
        从磁盘上获取插件设置。
        :return: 重新加载的插件设置
        """
        try:
            return self.model.read_from_file(self.path)
        except Exception as e:
            from ATRI.log import log
            from ATRI.exceptions import str_traceback
            log.error(f"加载配置文件错误:{str_traceback(e)}")
            return self.model()

    def config(self):
        """
        获取插件设置。
        :return: 内存中的插件设置。
        """
        return self._config

    def change_config(self, value: BaseModel = None):
        """
        修改插件设置。
        :param value: 修改后的插件设置模型
        """
        if value is None:
            self._config.write_into_file(self.path)
            return
        value.write_into_file(self.path)

    def __enter__(self):
        return self._config

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self._config.write_into_file(self.path)
        return False

    @classmethod
    def get(cls, service: str):
        """
        获取指定服务的插件设置。
        :param service: 服务名
        :return: 若存在返回PluginConfig对象，不存在返回None
        """
        if service in plugin_config:
            return plugin_config[service]
        else:
            return None
