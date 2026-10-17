from ATRI.exceptions import str_traceback

from ..utils import log
from .contents import (
    LLMContent,
    LLMContents,
    LLMMessage,
    LLMMessages,
    Sys1Content,
    Sys1Question,
)
from .embedding import create_embedding_model
from .gemini import create_gemini_model
from .model import (
    ALL_MODEL_RESP_ERR,
    NO_MODEL_ERR,
    LLMManager,
    LLMResponse,
    ModelRequestError,
    ModelType,
)
from .openai import create_openai_model
from .provider import LLMModel, LLMProvider, ModelPricing, ProviderManager
from .system_one import SystemOneModel, create_sys1_model
from .tts import create_tts_model

provider_type = {
    "openai": create_openai_model,
    "gemini": create_gemini_model,
    "embedding": create_embedding_model,
    "tts": create_tts_model,
    "sys1": create_sys1_model,
}

llm_manager = LLMManager()


def load_models_from_config():
    llm_manager.clear_all()
    from ..config import load_provider_from_config

    load_provider_from_config()
    for _provider in ProviderManager.providers.values():
        if _provider.provider_type in provider_type:
            for m in _provider.models:
                type_ = [m.type] if isinstance(m.type, str) else m.type
                type_ = [t for t in type_ if t]
                if not type_:
                    log.warning(f"{m.name}没有配置模型类型，已跳过")
                    continue
                for t in type_:
                    if t in {model_type.value for model_type in ModelType}:
                        try:
                            llm_manager.register_model(
                                model=provider_type[_provider.provider_type](
                                    name=m.name,
                                    model=m.model,
                                    endpoint=_provider.url,
                                    api_key=_provider.api_key,
                                    temperature=m.temperature,
                                    data=m.data,
                                ),
                                model_type=t,
                            )
                        except Exception as e:
                            log.error(f"注册模型失败：{m.name}\n{str_traceback(e)}")
                    else:
                        log.warning(f"{m.name}的{t}暂不支持")
        else:
            log.warning(f"{_provider.provider_name}的{_provider.provider_type}暂不支持")


__all__ = [
    "ALL_MODEL_RESP_ERR",
    "NO_MODEL_ERR",
    "LLMContent",
    "LLMContents",
    "LLMManager",
    "LLMMessage",
    "LLMMessages",
    "LLMModel",
    "LLMProvider",
    "LLMResponse",
    "ModelPricing",
    "ModelRequestError",
    "ModelType",
    "Sys1Content",
    "Sys1Question",
    "SystemOneModel",
    "create_embedding_model",
    "create_gemini_model",
    "create_openai_model",
    "create_sys1_model",
    "create_tts_model",
    "llm_manager",
    "load_models_from_config",
    "provider_type",
]
