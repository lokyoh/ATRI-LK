from .model import LLMModel
from .registry import ProviderSpec, register_provider


def create_openai_model(
    name,
    model,
    endpoint,
    api_key,
    temperature,
    data=None,
    **kwargs,
):
    return LLMModel(
        name=name,
        model=model,
        endpoint=endpoint,
        api_key=api_key,
        temperature=temperature,
        data=data,
        **kwargs,
    )


register_provider(
    ProviderSpec(
        key="openai",
        label="OpenAI 兼容",
        factory=create_openai_model,
    )
)
