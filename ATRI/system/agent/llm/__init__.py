from ATRI.exceptions import str_traceback

from .model import LLMManager, ModelType, NO_MODEL_ERR, ALL_MODEL_RESP_ERR
from .openai import create_openai_model
from .gemini import create_gemini_model

from .provider import ProviderManager
from ..utils import log

provider_type = {
    'openai': create_openai_model,
    'gemini': create_gemini_model,
}

llm_manager = LLMManager()


def load_models_from_config():
    llm_manager.clear_all()
    from ..config import load_provider_from_config
    load_provider_from_config()
    for _provider in ProviderManager.providers.values():
        if _provider.provider_type in provider_type:
            for m in _provider.models:
                if isinstance(m.type, str):
                    _type = [m.type]
                else:
                    _type = m.type
                for t in _type:
                    if t in ModelType:
                        try:
                            llm_manager.register_model(model=provider_type[_provider.provider_type](
                                name=m.name,
                                model=m.model,
                                endpoint=_provider.url,
                                api_key=_provider.api_key,
                                temperature=m.temperature,
                            ), model_type=t)
                        except Exception as e:
                            log.error(f'注册模型失败：{m.name}\n{str_traceback(e)}')
                    else:
                        log.warning(f'{m.name}的{t}暂不支持')
        else:
            log.warning(f'{_provider.provider_name}的{_provider.provider_type}暂不支持')
