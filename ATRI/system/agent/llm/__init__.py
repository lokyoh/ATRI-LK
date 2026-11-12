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
from .embedding import create_embedding_model
from .gemini import create_gemini_model
from .model import (
    AGENT_TYPES,
    ALL_MODEL_RESP_ERR,
    NO_MODEL_ERR,
    AgentModelMissingError,
    LLMManager,
    LLMResponse,
    ModelRequestError,
    ModelType,
)
from .openai import create_openai_model
from .provider import (
    LLMModel,
    LLMProvider,
    LLMProviderKey,
    ModelPricing,
    ProviderManager,
)
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
                    logger.warning(f"{m.name}没有配置模型类型，已跳过")
                    continue
                for t in type_:
                    if t == "atri":
                        logger.warning(
                            f"{m.name} 使用了已废弃的模型类型 'atri'，已跳过；"
                            "请改为 atri-think / atri-chat 等具体类型"
                        )
                        continue
                    if t in {model_type.value for model_type in ModelType}:
                        try:
                            selected_keys = _provider.keys_for_model(m)
                            key_values = [
                                item.key for item in selected_keys if item.key
                            ]
                            api_key = (
                                key_values[0]
                                if len(key_values) == 1
                                else key_values or None
                            )
                            model = provider_type[_provider.provider_type](
                                name=m.name,
                                model=m.model,
                                endpoint=_provider.url,
                                api_key=api_key,
                                temperature=m.temperature,
                                data=m.data,
                            )
                            llm_manager.register_model(model=model, model_type=t)
                        except Exception as e:
                            logger.error(f"注册模型失败：{m.name}\n{str_traceback(e)}")
                    else:
                        logger.warning(f"{m.name}的{t}暂不支持")
        else:
            logger.warning(
                f"{_provider.provider_name}的{_provider.provider_type}暂不支持"
            )


__all__ = [
    "AGENT_TYPES",
    "ALL_MODEL_RESP_ERR",
    "NO_MODEL_ERR",
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
