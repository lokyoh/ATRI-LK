import os
import shutil
import sys
from pathlib import Path
from time import sleep

import yaml

from .create import init_config
from .models import BotConfig, ConfigModel, RuntimeConfig

CONFIG_DATA_PATH = Path(".") / "data" / "config"
CONFIG_DATA_PATH.mkdir(parents=True, exist_ok=True)

_DEFAULT_CONFIG_PATH = Path(".") / "res" / "default_config.yml"


class Config:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        if not config_path.is_file():
            init_config(config_path, _DEFAULT_CONFIG_PATH)
            sleep(3)

        raw_conf = yaml.safe_load(_DEFAULT_CONFIG_PATH.read_bytes())
        conf = yaml.safe_load(config_path.read_bytes())
        r_c_v = raw_conf.get("ConfigVersion")
        c_v = conf.get("ConfigVersion")

        if tuple(map(int, r_c_v.split('.')[:2])) != tuple(map(int, c_v.split('.')[:2])):
            shutil.copy2(config_path, config_path.with_name('config_backup.yml'))
            os.remove(config_path)
            print("!!! 你的 config.yml 文件已废弃,已自动为你备份为 config_backup.yml 并删除原文件,请重新启动重新配置")
            sleep(3)
            sys.exit(-1)

        self.config = conf
        self.config_model: ConfigModel = ConfigModel.model_validate(self.config)

        if r_c_v != c_v:
            shutil.copy2(config_path, config_path.with_name('config_backup.yml'))
            self.config_model.ConfigVersion = r_c_v
            self.save_conf()
            print("!!! 你的 config.yml 文件已过时,已自动为你备份并自动更新")

    def save_conf(self):
        self.config = self.config_model.model_dump()
        with open(self.config_path, 'w', encoding='utf-8') as file:
            yaml.dump(self.config, file, allow_unicode=True, default_flow_style=False, sort_keys=False)

    def get_runtime_conf(self) -> dict:
        bot_conf = BotConfig.model_validate(self.config["BotConfig"])

        return RuntimeConfig(
            host=bot_conf.host,
            port=bot_conf.port,
            debug=bot_conf.debug,
            superusers=bot_conf.superusers,
            nickname=bot_conf.nickname,
            onebot_access_token=bot_conf.access_token,
            command_start=bot_conf.command_start,
            command_sep=bot_conf.command_sep,
            session_expire_timeout=bot_conf.session_expire_timeout,
        ).model_dump()
