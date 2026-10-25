from .config import config as config
from .service import plugin as plugin


def reload_all():
    from .config import reload_config

    reload_config()

    from .llm import load_models_from_config

    load_models_from_config()

    from .agent.function_calling import register_function_calling

    register_function_calling()

    from .agent.function_plugin.manage import function_plugin_manager

    function_plugin_manager.reload_all()

    from .agent.skills import skill_manager

    skill_manager.invalidate()
