import os

import yaml

from ATRI.configs import PluginConfig
from ATRI.dir import SYS_CONFIG_DIR
from ATRI.log import log
from ATRI.utils.model import BaseModel

from ...exceptions import str_traceback
from .llm.provider import LLMProvider, ProviderManager, default_provider
from .service import plugin

PROVIDER_CONFIG_FILE = SYS_CONFIG_DIR / "provider.yml"


class SearchConfig(BaseModel):
    enable: bool = False
    provider: str = "tavily"
    api_key: str = ""


class TTSConfig(BaseModel):
    enable: bool = False
    model: str = ""
    url: str = ""
    voice_id: str = ""
    api_key: str = ""


class EmbeddingConfig(BaseModel):
    enable: bool = False
    model: str = ""
    url: str = ""
    api_key: str = ""


class AgentConfig(BaseModel):
    """
    agent插件设置:
    """

    max_history: int = 20
    search: SearchConfig = SearchConfig()
    tts: TTSConfig = TTSConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()


config_manager: PluginConfig = plugin.add_plugin_config(AgentConfig)
config: AgentConfig = config_manager.config()


def reload_config():
    global config
    config = plugin.plugin_config().load_config()


def load_provider_from_config():
    ProviderManager.clear_all()
    if not os.path.exists(PROVIDER_CONFIG_FILE):
        os.makedirs(os.path.dirname(PROVIDER_CONFIG_FILE), exist_ok=True)
        with open(PROVIDER_CONFIG_FILE, "w", encoding="utf-8") as file:
            yaml.dump(
                {default_provider.provider_name: default_provider.model_dump()},
                file,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )
        return
    provider_data: dict = yaml.safe_load(PROVIDER_CONFIG_FILE.read_bytes())
    for _provider, value in provider_data.items():
        try:
            ProviderManager.register(LLMProvider(**value))
        except Exception as e:
            log.error(f"{_provider}无效配置:\n{str_traceback(e)}")
