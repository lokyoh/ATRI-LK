from .config import config
from .service import plugin


def reload_all():
    from .config import reload_config
    reload_config()
    from .llm import load_models_from_config
    load_models_from_config()
    from .agent.function_calling import register_function_calling
    register_function_calling()
