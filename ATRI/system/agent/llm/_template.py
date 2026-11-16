"""新增供应商的模板：复制改名，写工厂 + 底部 register_provider，
再把模块加进 llm/__init__.py 的 import 即可。"""

from .model import LLMModel
from .registry import ProviderSpec, register_provider


def create_example_model(
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
        key="example",
        label="示例供应商",
        factory=create_example_model,
        endpoint="https://api.example.com/v1",
    )
)
