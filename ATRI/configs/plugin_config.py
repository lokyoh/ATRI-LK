from pathlib import Path
from typing import Type
import os

from ATRI.utils.model import BaseModel

plugin_config = {}
CONFIG_DIR = Path(".") / "data" / "config"


class PluginConfig:
    def __init__(self, service: str, model: Type[BaseModel]):
        self.model = model
        self.path = CONFIG_DIR / f"{service}_config.json"
        if not os.path.exists(self.path):
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            self.change_config(self.model())
        self._config = self.load_config()
        plugin_config[service] = self

    def load_config(self):
        try:
            return self.model.read_from_file(self.path)
        except Exception as e:
            from ATRI.log import log
            from ATRI.exceptions import str_traceback
            log.error(f"加载配置文件错误:{str_traceback(e)}")
            return self.model()

    def config(self):
        return self._config

    def change_config(self, value: BaseModel = None):
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
        if service in plugin_config:
            return plugin_config[service]
        else:
            return None
