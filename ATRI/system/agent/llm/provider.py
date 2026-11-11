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
    if isinstance(value, tuple | set | list):
        normalized = []
        for item in value:
            if isinstance(item, str):
                item = item.strip()
                if item:
                    normalized.append(item)
        return normalized
    return []


def validate_api_url(value: Any) -> str:
    """校验 API 端点地址，拒绝控制字符与非法协议。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("url 必须是字符串")
    if _FORBIDDEN_CTRL_RE.search(value):
        raise ValueError("url 含有非法控制字符")
    if value and not (value.startswith("http://") or value.startswith("https://")):
        raise ValueError("url 必须以 http:// 或 https:// 开头")
    return value.rstrip("/")


class LLMProviderKey(BaseModel):
    """供应商下的一个 API 密钥，可命名后被模型按名称选择。"""

    name: str = ""
    """密钥名称，在同一个供应商内唯一"""
    key: str = ""
    """密钥内容"""
    previous_name: str = Field("", exclude=True)
    """仅用于 WebUI 编辑：重命名前的名称，不会写入配置文件"""

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: Any) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError("密钥名称必须是字符串")
        return value.strip()

    @field_validator("key", mode="before")
    @classmethod
    def validate_key(cls, value: Any) -> str:
        if value is None:
            return ""
        return validate_api_key(value)


def normalize_provider_keys(value: Any) -> list[LLMProviderKey]:
    """把 provider 的 api_key 统一成命名密钥列表。

    兼容已提交版本的 ``api_key: "sk-xxx"``；新版列表输入必须是命名密钥对象。
    """
    if value is None or value == "":
        return []
    if isinstance(value, LLMProviderKey):
        return [value]
    if isinstance(value, dict):
        return [LLMProviderKey.model_validate(value)]
    if isinstance(value, str):
        return [LLMProviderKey(key=value)]
    if isinstance(value, tuple | set | list):
        keys: list[LLMProviderKey] = []
        for item in value:
            if isinstance(item, LLMProviderKey):
                keys.append(item)
            elif isinstance(item, dict):
                keys.append(LLMProviderKey.model_validate(item))
            else:
                raise ValueError("api_key 列表项必须是命名密钥对象")
        return keys
    raise ValueError("api_key 必须是字符串或命名密钥对象列表")


def ensure_key_names(keys: list[LLMProviderKey]) -> list[LLMProviderKey]:
    """为未命名的密钥生成稳定名称，并保证同一 provider 内名称唯一。"""
    seen: set[str] = set()
    for index, item in enumerate(keys):
        base = item.name or f"key-{index + 1}"
        name = base
        suffix = 2
        while name in seen:
            name = f"{base}-{suffix}"
            suffix += 1
        seen.add(name)
        item.name = name
    return keys


def normalize_key_selectors(value: Any) -> list[str]:
    """规范化模型选择的密钥名称列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, tuple | set | list):
        return []
    selectors: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        selector = item.strip()
        if selector and selector not in selectors:
            selectors.append(selector)
    return selectors


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
    api_key: str | list[str] = ""
    """选择的密钥名称；为空表示使用 provider 的第一个密钥"""
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

    @field_validator("api_key", mode="before")
    @classmethod
    def validate_api_key(cls, value: Any) -> str | list[str]:
        normalized = normalize_key_selectors(value)
        if not normalized:
            return ""
        return normalized[0] if len(normalized) == 1 else normalized


class LLMProvider(BaseModel):
    """
    llm模型提供商配置:

    - provider 负责连接信息与鉴权
    - ``url`` 只能有一个；``api_key`` 是带名称的密钥列表
    """

    provider_name: str = ""
    provider_type: str = ""
    url: str = ""
    """API 基础地址，仅支持单个 URL"""
    api_key: list[LLMProviderKey] = Field(default_factory=list)
    """供应商下的命名密钥列表；模型可以按名称选择其中一个或多个"""
    models: list[LLMModel] = Field(default_factory=list)

    @field_validator("api_key", mode="before")
    @classmethod
    def validate_api_key_field(cls, value: Any) -> list[LLMProviderKey]:
        return ensure_key_names(normalize_provider_keys(value))

    @field_validator("url", mode="before")
    @classmethod
    def validate_url(cls, value: Any) -> str:
        return validate_api_url(value)

    def keys_for_model(self, model: LLMModel) -> list[LLMProviderKey]:
        """返回模型实际使用的密钥列表。

        - 模型未填写 ``api_key``：使用 provider 的第一个密钥；
        - 填写名称：按名称选择；
        - 填写 ``all`` / ``*``：使用 provider 的全部密钥；
        - 名称未匹配时按密钥原文处理，允许模型直接覆盖为自己填写的密钥。
        """
        if not self.api_key:
            return []
        if not model.api_key:
            return [self.api_key[0]]
        selectors = [model.api_key] if isinstance(model.api_key, str) else model.api_key
        if any(selector.lower() in {"all", "*"} for selector in selectors):
            return list(self.api_key)
        by_name = {item.name: item for item in self.api_key}
        by_value = {item.key: item for item in self.api_key if item.key}
        selected: list[LLMProviderKey] = []
        for selector in selectors:
            item = by_name.get(selector) or by_value.get(selector)
            if item is None:
                item = LLMProviderKey(name=f"model-{len(selected) + 1}", key=selector)
            if item not in selected:
                selected.append(item)
        return selected


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
        """添加模型；同名时覆盖，避免同一供应商下出现重名模型。"""
        if provider_name in cls.providers:
            provider = cls.providers[provider_name]
            provider.models = [
                existing for existing in provider.models if existing.name != model.name
            ]
            provider.models.append(model)

    @classmethod
    def save_provider_config(cls):
        from ATRI.system.agent.config import (
            PROVIDER_CONFIG_FILE,
            _apply_model_preferences,
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
        # ``ProviderManager`` 只描述“有哪些模型”，真正被调用的是
        # ``llm_manager`` 里注册的实例。只重载 provider 会让新增、删除或修改
        # 的模型在重启前一直沿用旧实例——WebUI 的下拉选项也取自
        # ``llm_manager``，于是出现“模型改了、配置里还是旧模型”。
        # 这里连模型一起重建，并按当前 agent 配置恢复“指定模型”。
        from ATRI.system.agent.llm import load_models_from_config

        load_models_from_config()
        _apply_model_preferences()


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
