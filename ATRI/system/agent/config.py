import os

import yaml

from ATRI.configs import Field, PluginConfig
from ATRI.dir import SYS_CONFIG_DIR
from ATRI.log import log

from ...exceptions import str_traceback
from .llm.provider import LLMProvider, ProviderManager, default_provider
from .service import plugin

PROVIDER_CONFIG_FILE = SYS_CONFIG_DIR / "provider.yml"


class AgentConfig(PluginConfig):
    max_history = Field(
        int,
        30,
        "最大历史消息数",
        "包含Agent自己发送的消息,提高此值可使Agent获取更多历史聊天,但也会消耗更多Token,默认值30",
        min=10,
    )
    max_thinking_times = Field(
        int,
        20,
        "最大思考轮数",
        "允许Agent最高思考次数,当遇到需要大量思考情况可提高此值,默认值20",
        min=5,
    )
    search = Field(
        dict,
        "搜索API配置",
        children={
            "enable": Field(bool, False, "启用联网搜索"),
            "provider": Field(str, "tavily", "搜索服务"),
            "api_key": Field(str, "", "搜索 API 密钥"),
        },
    )


config: AgentConfig = plugin.add_plugin_config(AgentConfig)


def reload_config():
    config.load()


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
