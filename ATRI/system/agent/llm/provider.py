import re
from typing import Any, ClassVar

import yaml
from pydantic import Field, field_validator

from ATRI.utils.model import BaseModel

_FORBIDDEN_CTRL_RE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")
_API_KEY_RE = re.compile(r"^[A-Za-z0-9._\-:/+=]{1,4096}$")

PRICING_UNIT_TOKENS = 1_000_000
"""计费单价的计量基数：元 / 每 百万 Token"""


def validate_api_key(value: str) -> str:
    """校验用户填写的 API key，拒绝控制字符、空白、超长与非法字符。"""
    if not isinstance(value, str):
        raise ValueError("api_key 必须是字符串")
    if value == "":
        return ""
    if value != value.strip():
        raise ValueError("api_key 首尾不允许空白字符")
    if _FORBIDDEN_CTRL_RE.search(value):
        raise ValueError("api_key 含有非法控制字符（如换行、回车）")
    if not _API_KEY_RE.match(value):
        raise ValueError("api_key 含有不允许的字符")
    return value


def normalize_model_type(value: str | list[str] | None) -> list[str]:
    """规范化模型类型，兼容单值字符串与列表配置。"""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if isinstance(value, (tuple, set, list)):
        normalized = []
        for item in value:
            if isinstance(item, str):
                item = item.strip()
                if item:
                    normalized.append(item)
        return normalized
    return []


class ModelPricing(BaseModel):
    """
    模型计费配置，三项单价均以「元 / 每百万 Token」为单位：

    - input：输入（prompt）Token 单价
    - output：输出（completion）Token 单价
    - cache_hit：输入中缓存命中部分的 Token 单价
    """

    input: float = 0.0
    """输入单价（元/1M tokens）"""
    output: float = 0.0
    """输出单价（元/1M tokens）"""
    cache_hit: float = 0.0
    """缓存命中输入单价（元/1M tokens）"""

    @field_validator("input", "output", "cache_hit", mode="before")
    @classmethod
    def validate_price(cls, value: Any) -> float:
        if value is None:
            return 0.0
        try:
            price = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("计费单价必须是数字") from exc
        if price != price or price in (float("inf"), float("-inf")):
            raise ValueError("计费单价必须是有限数字")
        if price < 0:
            raise ValueError("计费单价不能小于 0")
        return price

    def calculate_cost(
        self,
        prompt_tokens: int,
        output_tokens: int,
        cache_hit_tokens: int = 0,
    ) -> float:
        """按单价计算一次调用（或一段用量）的费用，单位为元。"""
        prompt = max(0, int(prompt_tokens))
        output = max(0, int(output_tokens))
        # 缓存命中是输入的子集，超出输入时按输入截断，避免出现负价
        cache_hit = min(max(0, int(cache_hit_tokens)), prompt)
        normal_input = prompt - cache_hit
        return (
            normal_input * self.input
            + cache_hit * self.cache_hit
            + output * self.output
        ) / PRICING_UNIT_TOKENS


class LLMModel(BaseModel):
    """
    llm模型配置:

    设计上遵循 provider + model 的组合方式：
    - provider 负责连接信息和鉴权
    - model 负责具体模型的名称、温度和类型
    """

    name: str = ""
    model: str = ""
    temperature: float = 0.7
    type: str | list[str] = ""
    pricing: ModelPricing | None = None
    """计费配置，None 表示未配置计费"""
    data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("temperature", mode="before")
    @classmethod
    def validate_temperature(cls, value: Any) -> float:
        if value is None:
            return 0.7
        try:
            temperature = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("temperature 必须是数字") from exc
        if temperature < 0:
            raise ValueError("temperature 不能小于 0")
        return temperature

    @field_validator("type", mode="before")
    @classmethod
    def validate_type(cls, value: Any) -> str | list[str]:
        normalized = normalize_model_type(value)
        if not normalized:
            return ""
        return normalized[0] if len(normalized) == 1 else normalized


class LLMProvider(BaseModel):
    """
    llm模型提供商配置:

    """

    provider_name: str = ""
    provider_type: str = ""
    url: str = ""
    api_key: str = ""
    models: list[LLMModel] = Field(default_factory=list)

    @field_validator("api_key", mode="before")
    @classmethod
    def validate_api_key_field(cls, value: Any) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError("api_key 必须是字符串")
        return validate_api_key(value)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if value == "":
            return value
        if not isinstance(value, str):
            raise ValueError("url 必须是字符串")
        if _FORBIDDEN_CTRL_RE.search(value):
            raise ValueError("url 含有非法控制字符")
        if not (value.startswith("http://") or value.startswith("https://")):
            raise ValueError("url 必须以 http:// 或 https:// 开头")
        return value.rstrip("/")


class ProviderManager:
    """
    模型提供商管理器
    """

    providers: ClassVar[dict[str, LLMProvider]] = {}

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
        return [provider.model_copy(deep=True) for provider in cls.providers.values()]

    @classmethod
    def del_provider(cls, provider_name: str):
        if provider_name in cls.providers:
            del cls.providers[provider_name]

    @classmethod
    def set_provider(cls, provider_name: str, provider: LLMProvider):
        cls.providers[provider_name] = provider

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
        from ATRI.system.agent.config import (
            PROVIDER_CONFIG_FILE,
            load_provider_from_config,
        )

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
        load_provider_from_config()


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
