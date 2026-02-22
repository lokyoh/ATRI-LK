import yaml
from time import sleep
from pathlib import Path

from .create import init_config
from .models import BotConfig, ConfigModel, RuntimeConfig

CONFIG_DATA_PATH = Path(".") / "data" / "config"
CONFIG_DATA_PATH.mkdir(parents=True, exist_ok=True)

_DEFAULT_CONFIG_PATH = Path(".") / "res" / "default_config.yml"


class Config:
    def __init__(self, config_path: Path):
        if not config_path.is_file():
            init_config(config_path, _DEFAULT_CONFIG_PATH)
            sleep(3)

        raw_conf = yaml.safe_load(_DEFAULT_CONFIG_PATH.read_bytes())
        conf = yaml.safe_load(config_path.read_bytes())

        if raw_conf.get("ConfigVersion") != conf.get("ConfigVersion"):
            print("!!! 你的 config.yml 文件已废弃, 请 删除/备份 并重新启动")
            sleep(3)
            exit(-1)

        self.config = conf
        self.config_model: ConfigModel = ConfigModel.model_validate(self.config)

    def save_conf(self):
        self.config = self.config_model.model_dump()
        with open(CONFIG_DATA_PATH, 'w', encoding='utf-8') as file:
            yaml.dump(self.config, file, allow_unicode=True, default_flow_style=False)

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
