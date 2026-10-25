from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .manage import FunctionPluginManager as FunctionPluginManager
    from .manage import function_plugin_manager as function_plugin_manager
    from .model import FunctionPlugin as FunctionPlugin
    from .model import PluginSkill as PluginSkill
    from .model import PluginSkillFile as PluginSkillFile
    from .registration import register_function_plugin as register_function_plugin

__all__ = [
    "FunctionPlugin",
    "FunctionPluginManager",
    "PluginSkill",
    "PluginSkillFile",
    "function_plugin_manager",
    "register_function_plugin",
]


def __getattr__(name: str) -> Any:
    """按需导入，`import function_plugin` 本身不触碰任何子模块。"""
    if name in {"FunctionPlugin", "PluginSkill", "PluginSkillFile"}:
        from .model import FunctionPlugin, PluginSkill, PluginSkillFile

        return {
            "FunctionPlugin": FunctionPlugin,
            "PluginSkill": PluginSkill,
            "PluginSkillFile": PluginSkillFile,
        }[name]
    if name in {"FunctionPluginManager", "function_plugin_manager"}:
        from .manage import FunctionPluginManager, function_plugin_manager

        return (
            FunctionPluginManager
            if name == "FunctionPluginManager"
            else function_plugin_manager
        )
    if name == "register_function_plugin":
        from .registration import register_function_plugin

        return register_function_plugin
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
