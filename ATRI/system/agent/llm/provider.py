import yaml

from ATRI.utils.model import BaseModel


class LLMModel(BaseModel):
    """
    llm模型配置:

    """

    name: str = ""
    model: str = ""
    temperature: float = 0.7
    type: str | list[str] = ""


class LLMProvider(BaseModel):
    """
    llm模型提供商配置:

    """

    provider_name: str = ""
    provider_type: str = ""
    url: str = ""
    api_key: str = ""
    models: list[LLMModel] = []


class ProviderManager:
    """
    模型提供商管理器
    """

    providers: dict[str, LLMProvider] = {}

    @classmethod
    def register(cls, provider: LLMProvider):
        if provider.provider_name in cls.providers:
            raise RuntimeError("已存在同名的模型提供商")
        cls.providers[provider.provider_name] = provider

    @classmethod
    def clear_all(cls):
        cls.providers.clear()

    @classmethod
    def get_provider_list(cls) -> list[LLMProvider]:
        return list(cls.providers.values())

    @classmethod
    def del_provider(cls, provider_name: str):
        if provider_name in cls.providers:
            del cls.providers[provider_name]

    @classmethod
    def del_model(cls, provider_name: str, model_name: str):
        if provider_name in cls.providers:
            provider = cls.providers[provider_name]
            provider.models = [
                model for model in provider.models if model.name != model_name
            ]

    @classmethod
    def add_model(cls, provider_name: str, model: LLMModel):
        if provider_name in cls.providers:
            provider = cls.providers[provider_name]
            provider.models.append(model)

    @classmethod
    def save_provider_config(cls):
        from ATRI.system.agent.config import PROVIDER_CONFIG_FILE

        provider_data = {
            provider.provider_name: provider.model_dump()
            for provider in cls.providers.values()
        }
        with open(PROVIDER_CONFIG_FILE, "w", encoding="utf-8") as file:
            yaml.dump(
                provider_data,
                file,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )


default_provider = LLMProvider(
    provider_name="siliconflow",
    provider_type="openai",
    url="https://api.siliconflow.cn/v1",
    models=[
        LLMModel(
            name="siliconflow/DeepSeek-V4-Flash",
            model="deepseek-ai/DeepSeek-V4-Flash",
            temperature=0.7,
            type="chat",
        ),
        LLMModel(
            name="siliconflow/qwen3-vl-30",
            model="Qwen/Qwen3-VL-30B-A3B-Instruct",
            temperature=0.7,
            type="image",
        ),
        LLMModel(
            name="siliconflow/qwen3-30B",
            model="Qwen/Qwen3-30B-A3B-Instruct-2507",
            temperature=0.3,
            type="tool",
        ),
    ],
)
