from .config import config as config
from .service import plugin as plugin


def reload_all():
    """重新加载配置、模型与功能注册。

    顺序有硬约束：**必须先加载模型，再应用运行时配置**。
    ``apply_runtime_config`` 里的「指定模型」要按名字到已注册的模型里查，
    模型还没注册时查不到，指定会被判为无效并丢弃（只剩一条告警）。
    原先这里是 reload_config() 在前，导致重载后模型指定静默失效。
    """
    from .config import reload_config

    reload_config()

    from .llm import load_models_from_config

    load_models_from_config()

    from .config import apply_runtime_config

    apply_runtime_config()

    from .agent.function_calling import register_function_calling

    register_function_calling()

    from .agent.function_plugin.manage import function_plugin_manager

    function_plugin_manager.reload_all()

    from .agent.skills import skill_manager

    skill_manager.invalidate()
