from pathlib import Path
from typing import ClassVar

from ..file.reader import read_file
from ..file.tree_string import default_ignore_dirs, get_tree_string
from ..function_calling import (
    FunctionCalling,
    FunctionCallingArg,
    FunctionCallingData,
)
from ..skills.loader import FUNCTION_PLUGIN_SKILLS_DIR
from .model import FunctionPlugin, PluginSkillFile


class GetDirTreeFunctionCalling(FunctionCalling):
    """查询目录树。"""

    function_name = "get_dir_tree"
    description = "查询文件目录:root:不包含后续目录根目录;ATRI:系统代码目录;system_plugins:系统插件目录;plugins:插件目录;config:配置目录;errors:最近7天的错误报告目录"
    args: ClassVar[list[FunctionCallingArg]] = [
        FunctionCallingArg(name="dir", _type="str", description="目录名")
    ]

    @staticmethod
    async def call(data: FunctionCallingData):
        dir_name = data.data.get("dir", "")
        if dir_name == "root":
            path = Path(".")
            return f"{path.resolve()}/\n" + get_tree_string(
                path, ignore_dirs={"ATRI", "data", "plugins"} | default_ignore_dirs
            )
        if dir_name == "ATRI":
            path = Path(".") / "ATRI"
            return f"{path.resolve()}/\n" + get_tree_string(
                path, ignore_dirs={"system"} | default_ignore_dirs
            )
        if dir_name == "system_plugins":
            path = Path(".") / "ATRI" / "system"
            return f"{path.resolve()}/\n" + get_tree_string(path)
        if dir_name == "plugins":
            path = Path(".") / "plugins"
            return f"{path.resolve()}/\n" + get_tree_string(path)
        if dir_name == "config":
            path = Path(".") / "data" / "config"
            return f"{path.resolve()}/\n" + get_tree_string(path)
        if dir_name == "errors":
            path = Path(".") / "data" / "errors"
            return f"{path.resolve()}/\n" + get_tree_string(path, recent_days=7)
        return "请提供正确的目录名。"


class ReadFileFunctionCalling(FunctionCalling):
    """读取文件内容。"""

    function_name = "read_file"
    description = "读取指定文件"
    args: ClassVar[list[FunctionCallingArg]] = [
        FunctionCallingArg(name="path", _type="str", description="文件路径"),
        FunctionCallingArg(
            name="start", _type="int", description="可选,起始行数,默认1"
        ),
        FunctionCallingArg(name="end", _type="int", description="可选,结束行数,默认0"),
    ]

    @staticmethod
    async def call(data: FunctionCallingData):
        path = data.data.get("path", "")
        start = data.data.get("start", 1)
        end = data.data.get("end", 0)
        return read_file(path, start, end)


BUILTIN_FUNCTION_PLUGINS: list[FunctionPlugin] = [
    FunctionPlugin(
        name="file_ops",
        description="文件操作：查目录树与读文件。仅由智能体自查代码/配置时使用",
        skill_files=[
            PluginSkillFile(
                path=FUNCTION_PLUGIN_SKILLS_DIR / "file_ops" / "file_ops.md"
            )
        ],
        rare_functions=[GetDirTreeFunctionCalling, ReadFileFunctionCalling],
    ),
]


def register_builtin_function_plugins():
    from .manage import function_plugin_manager

    function_plugin_manager.register_many(
        [plugin.model_copy(deep=True) for plugin in BUILTIN_FUNCTION_PLUGINS]
    )
