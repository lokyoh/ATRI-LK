"""DeepSeek 官方接口。

与 OpenAI 兼容层的三点差异：

- 推理模型（reasoner / r1）拒收 temperature，带上直接 400；
- 正文可能为空，思考内容在 reasoning_content 里；
- 缓存命中字段两种形状都要认。
"""

import re
from typing import Any

from .model import LLMModel, LLMResponse
from .registry import ProviderSpec, register_provider
from .token import TokenUsage

#: 不接受采样参数的模型名。
_NO_SAMPLING_RE = re.compile(r"reasoner|r1", re.IGNORECASE)

DEFAULT_ENDPOINT = "https://api.deepseek.com"


class DeepSeekModel(LLMModel):
    @property
    def supports_sampling(self) -> bool:
        return not _NO_SAMPLING_RE.search(self.model)

    def _build_payload(self, messages: list[dict], cache_key: str | None) -> dict:
        data = {"model": self.model, "messages": messages, **self.data}
        if self.supports_sampling:
            data.setdefault("temperature", self.temperature)
        if self.cache_field and cache_key:
            data.setdefault(self.cache_field, cache_key)
        if self.stream:
            data.setdefault("stream", True)
            # 流式默认不回 usage。
            data.setdefault("stream_options", {"include_usage": True})
        return data

    @staticmethod
    def _usage_from_dict(usage: dict[str, Any]) -> TokenUsage:
        hit = usage.get("prompt_cache_hit_tokens")
        if hit is None:
            hit = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        return TokenUsage(
            prompt_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            cache_hit_tokens=int(hit or 0),
        )

    @staticmethod
    def _message_text(message: dict) -> str:
        text = message.get("content") or message.get("reasoning_content") or ""
        return str(text).lstrip("\n")

    def _parse_response(self, resp_json: dict) -> LLMResponse:
        message = (resp_json.get("choices") or [{}])[0].get("message", {})
        return LLMResponse(
            content=self._message_text(message),
            usage=self._usage_from_dict(resp_json.get("usage", {})),
        )


def create_deepseek_model(
    name,
    model,
    endpoint,
    api_key,
    temperature,
    data=None,
    **kwargs,
):
    return DeepSeekModel(
        name=name,
        model=model,
        endpoint=endpoint or DEFAULT_ENDPOINT,
        api_key=api_key,
        temperature=temperature,
        data=data,
        **kwargs,
    )


register_provider(
    ProviderSpec(
        key="deepseek",
        label="DeepSeek 官方",
        factory=create_deepseek_model,
        endpoint=DEFAULT_ENDPOINT,
        cache_field="user_id",
    )
)
