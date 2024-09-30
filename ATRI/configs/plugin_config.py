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
        return self.model.read_from_file(self.path)

    def change_config(self, value: BaseModel):
        value.write_into_file(self.path)
