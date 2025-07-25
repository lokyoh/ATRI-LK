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
            self.change_config(model())
        plugin_config[service] = self

    def config(self):
        try:
            return self.model.read_from_file(self.path)
        except Exception as e:
            from ATRI.log import log
            log.error(f"加载配置文件错误:{e}")
            return self.model()

    def change_config(self, value: BaseModel):
        value.write_into_file(self.path)
