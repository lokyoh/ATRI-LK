from ATRI.log import log

from .manage import function_plugin_manager
from .model import FunctionPlugin, PluginSkill, PluginSkillFile


def register_function_plugin(
    name: str,
    description: str = "",
    skills: list[PluginSkill | str] | None = None,
    skill_files: list[PluginSkillFile | str] | None = None,
    functions: list | None = None,
    rare_functions: list | None = None,
) -> FunctionPlugin:
    """注册一个功能插件（自带技能正文与功能类一并登记）。

    返回归一化后的插件（成员只剩名字）。
    """
    plugin = FunctionPlugin(
        name=name,
        description=description,
        skills=list(skills or []),
        skill_files=list(skill_files or []),
        functions=list(functions or []),
        rare_functions=list(rare_functions or []),
    )
    stored = function_plugin_manager.register(plugin)
    log.debug(f"功能插件 {name} 通过 register_function_plugin 注册完成")
    return stored


__all__ = ["PluginSkill", "PluginSkillFile", "register_function_plugin"]
