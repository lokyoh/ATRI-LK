"""供应商类型注册表。

新增一种供应商 = 新建模块并 `register_provider`；WebUI 的类型下拉、
默认地址、连通性测试都从这里读。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .model import LLMModel

Factory = Callable[..., LLMModel]


@dataclass(frozen=True)
class ProviderSpec:
    """一种供应商类型：显示信息 + 造模型的工厂。"""

    key: str
    """provider_type 取值"""
    label: str
    """中文名"""
    factory: Factory
    endpoint: str = ""
    """官方默认地址，仅作输入框提示"""
    probe: Any = "请仅回复 OK。"
    """连通性测试内容"""
    probe_cleanup: bool = False
    """测试后删掉产物"""
    cache_field: str = ""
    """该供应商唯一的缓存标识字段；非空即锁定，用户不可改"""


PROVIDERS: dict[str, ProviderSpec] = {}
"""已注册类型，按注册顺序。"""

provider_type: dict[str, Factory] = {}
"""key → 工厂；可替换，供测试与扩展。"""


def register_provider(spec: ProviderSpec) -> ProviderSpec:
    """重复 key 直接报错。"""
    if spec.key in PROVIDERS:
        raise ValueError(f"供应商类型 {spec.key} 已经注册")
    PROVIDERS[spec.key] = spec
    provider_type[spec.key] = spec.factory
    return spec


def get_spec(key: str) -> ProviderSpec | None:
    return PROVIDERS.get(str(key))


def resolve_factory(key: str) -> Factory | None:
    """该类型当前的工厂；优先读 provider_type 以尊重替换。"""
    spec = PROVIDERS.get(str(key))
    if spec is None:
        return None
    return provider_type.get(spec.key, spec.factory)


def provider_keys() -> list[str]:
    return list(PROVIDERS)


__all__ = [
    "PROVIDERS",
    "Factory",
    "ProviderSpec",
    "get_spec",
    "provider_keys",
    "provider_type",
    "register_provider",
    "resolve_factory",
]
