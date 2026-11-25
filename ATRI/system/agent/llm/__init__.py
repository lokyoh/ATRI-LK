from ATRI.exceptions import str_traceback

from ..utils import log as logger
from .contents import (
    LLMContent,
    LLMContents,
    LLMMessage,
    LLMMessages,
    Sys1Content,
    Sys1Question,
)
from .deepseek import create_deepseek_model
from .edge_voice import create_edge_tts_model
from .edge_voice import ensure_default_provider as ensure_default_tts_provider
from .embedding import create_embedding_model
from .gemini import create_gemini_model
from .model import (
    ALL_MODEL_RESP_ERR,
    MODEL_TYPE_COLORS,
    MODEL_TYPE_LABELS,
    MODEL_TYPES,
    NO_MODEL_ERR,
    SLOT_SPECS,
    SLOTS,
    AgentModelMissingError,
    LLMManager,
    LLMResponse,
    ModelRequestError,
    ModelType,
    SlotSpec,
)

# 各模块 import 时自行注册；这里的顺序决定 WebUI 展示顺序。
from .openai import create_openai_model
from .provider import (
    CACHE_FIELD_OPTIONS,
    LLMModel,
    LLMProvider,
    LLMProviderKey,
    ModelPricing,
    ProviderManager,
)
from .registry import (
    PROVIDERS,
    ProviderSpec,
    get_spec,
    provider_keys,
    provider_type,
    register_provider,
    resolve_factory,
)
from .system_one import SystemOneModel, create_sys1_model
from .tts import create_tts_model

llm_manager = LLMManager()


def load_models_from_config():
    llm_manager.clear_all()
    from ..config import load_provider_from_config

    load_provider_from_config()
    # 内置 Edge 语音：没配语音模型时补一个，保证「语音回复」开箱可用。
    ensure_default_tts_provider()
    for _provider in ProviderManager.providers.values():
        spec = get_spec(_provider.provider_type)
        if spec is None:
            logger.warning(
                f"{_provider.provider_name}的{_provider.provider_type}暂不支持"
            )
            continue
        for m in _provider.models:
            type_ = [m.type] if isinstance(m.type, str) else m.type
            type_ = [t for t in type_ if t]
            if not type_:
                logger.warning(f"{m.name}没有配置模型类型，已跳过")
                continue
            for t in type_:
                if t not in MODEL_TYPES:
                    logger.warning(f"{m.name}的{t}暂不支持")
                    continue
                try:
                    selected_keys = _provider.keys_for_model(m)
                    key_values = [item.key for item in selected_keys if item.key]
                    api_key = (
                        key_values[0] if len(key_values) == 1 else key_values or None
                    )
                    # 供应商定死的字段优先，其余才看模型自己的配置。
                    cache_field = spec.cache_field or m.cache_field
                    model = resolve_factory(spec.key)(
                        name=m.name,
                        model=m.model,
                        endpoint=_provider.url,
                        api_key=api_key,
                        temperature=m.temperature,
                        data=m.data,
                        cache_field=cache_field,
                        cache_field_locked=bool(spec.cache_field),
                        stream=m.stream,
                    )
                    llm_manager.register_model(model=model, model_type=t)
                except Exception as e:
                    logger.error(f"注册模型失败：{m.name}\n{str_traceback(e)}")


__all__ = [
    "ALL_MODEL_RESP_ERR",
    "CACHE_FIELD_OPTIONS",
    "MODEL_TYPES",
    "MODEL_TYPE_COLORS",
    "MODEL_TYPE_LABELS",
    "NO_MODEL_ERR",
    "PROVIDERS",
    "SLOTS",
    "SLOT_SPECS",
    "AgentModelMissingError",
    "LLMContent",
    "LLMContents",
    "LLMManager",
    "LLMMessage",
    "LLMMessages",
    "LLMModel",
    "LLMProvider",
    "LLMProviderKey",
    "LLMResponse",
    "ModelPricing",
    "ModelRequestError",
    "ModelType",
    "ProviderSpec",
    "SlotSpec",
    "Sys1Content",
    "Sys1Question",
    "SystemOneModel",
    "create_deepseek_model",
    "create_edge_tts_model",
    "create_embedding_model",
    "create_gemini_model",
    "create_openai_model",
    "create_sys1_model",
    "create_tts_model",
    "ensure_default_tts_provider",
    "get_spec",
    "llm_manager",
    "load_models_from_config",
    "provider_keys",
    "provider_type",
    "register_provider",
    "resolve_factory",
]
