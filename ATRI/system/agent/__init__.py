from .config import config
from .service import plugin


def reload_all():
    from .config import reload_config
    reload_config()
    from .llm import load_models_from_config
    load_models_from_config()
    from .agent.function_calling import register_function_calling
    register_function_calling()
    # 技能正文/列表缓存在磁盘文件变更后不会自动失效，需在重载时清空
    from .agent.skills import skill_manager
    skill_manager.refresh()
