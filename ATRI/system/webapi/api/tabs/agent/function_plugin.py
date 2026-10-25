from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ATRI.exceptions import str_traceback
from ATRI.log import log
from ATRI.system.agent.agent.function_calling import (
    FunctionCallingEssentialError,
    FunctionCallingManager,
)
from ATRI.system.agent.agent.function_plugin.manage import function_plugin_manager
from ATRI.system.agent.agent.skills import skill_manager

from ....base_model import Result
from ....utils import authentication

router = APIRouter()


class PluginSkillInfo(BaseModel):
    name: str
    """技能名"""
    description: str
    """技能说明"""
    enabled: bool
    """技能自己的启用开关（不含插件整体开关）"""
    embedded: bool
    """技能自己的内嵌开关"""
    missing: bool = False
    """声明的技能当前不存在（插件声明与实际不符时置真）"""


class PluginFunctionInfo(BaseModel):
    name: str
    """功能名"""
    description: str
    """功能说明"""
    rare: bool
    """不常用功能：不进入提示词，只能通过 call 调用"""
    enabled: bool
    """功能自己的开关（不含插件整体开关）"""
    always_enabled: bool
    """核心功能：开关恒为开，不可关闭"""
    can_enable: bool
    """当前环境是否支持"""
    can_enable_reason: str = ""
    """环境不可用原因"""
    missing: bool = False
    """声明的功能当前未注册"""


class FunctionPluginInfo(BaseModel):
    name: str
    """插件名（开关状态的持久化键）"""
    description: str
    """插件说明"""
    enabled: bool
    """整体开关"""
    skills: list[PluginSkillInfo]
    """包含的技能（只开关，不可增删改）"""
    functions: list[PluginFunctionInfo]
    """包含的常用功能调用"""
    rare_functions: list[PluginFunctionInfo]
    """包含的不常用功能调用"""


class FunctionPluginState(BaseModel):
    name: str
    enabled: bool


class SetFunctionPluginModel(BaseModel):
    name: str
    enabled: bool


def _describe_skill(name: str) -> PluginSkillInfo:
    skill = skill_manager.get(name)
    if skill is None:
        return PluginSkillInfo(
            name=name,
            description="",
            enabled=False,
            embedded=False,
            missing=True,
        )
    return PluginSkillInfo(
        name=name,
        description=skill.description,
        enabled=skill.enabled,
        embedded=skill.embedded,
    )


def _describe_function(name: str, rare: bool) -> PluginFunctionInfo:
    func = FunctionCallingManager.function_calling.get(name)
    if func is None:
        return PluginFunctionInfo(
            name=name,
            description="",
            rare=rare,
            enabled=False,
            always_enabled=False,
            can_enable=False,
            missing=True,
        )
    check = FunctionCallingManager.get_can_enable(name)
    return PluginFunctionInfo(
        name=name,
        description=func.description,
        rare=rare,
        enabled=FunctionCallingManager.is_manually_enabled(name),
        always_enabled=FunctionCallingManager.is_essential(name),
        can_enable=check.available,
        can_enable_reason=check.reason,
    )


def _describe(plugin) -> FunctionPluginInfo:
    return FunctionPluginInfo(
        name=plugin.name,
        description=plugin.description,
        enabled=plugin.enabled,
        skills=[_describe_skill(name) for name in plugin.skills],
        functions=[_describe_function(name, False) for name in plugin.functions],
        rare_functions=[
            _describe_function(name, True) for name in plugin.rare_functions
        ],
    )


@router.get(
    "/get_function_plugins",
    dependencies=[authentication()],
    response_model=Result[list[FunctionPluginInfo]],
    response_class=JSONResponse,
    description="获取功能插件列表、包含的成员与开关状态",
)
async def _() -> Result[list[FunctionPluginInfo]]:
    try:
        return Result.ok(
            [_describe(plugin) for plugin in function_plugin_manager.list_all()],
            "拿到信息啦!",
        )
    except Exception as e:
        log.error(f"{router.prefix}/get_function_plugins 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/set_function_plugin",
    dependencies=[authentication()],
    response_model=Result[FunctionPluginState],
    response_class=JSONResponse,
    description="设置单个功能插件的整体开关",
)
async def _(payload: SetFunctionPluginModel) -> Result[FunctionPluginState]:
    try:
        if payload.name not in function_plugin_manager.names():
            return Result.fail(f"功能插件 {payload.name} 不存在", 404)
        function_plugin_manager.set_manually_enabled(payload.name, payload.enabled)
        return Result.ok(
            FunctionPluginState(
                name=payload.name,
                enabled=function_plugin_manager.is_manually_enabled(payload.name),
            ),
            "设置成功!",
        )
    except Exception as e:
        log.error(f"{router.prefix}/set_function_plugin 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/set_plugin_function_state",
    dependencies=[authentication()],
    response_model=Result[FunctionPluginInfo],
    response_class=JSONResponse,
    description="设置功能插件内单个功能调用的开关",
)
async def _(payload: SetFunctionPluginModel) -> Result[FunctionPluginInfo]:
    try:
        owner = function_plugin_manager.owner_of_function(payload.name)
        if owner is None:
            return Result.fail(f"功能 {payload.name} 不属于任何功能插件", 404)
        if not payload.enabled and FunctionCallingManager.is_essential(payload.name):
            return Result.fail(f"功能 {payload.name} 是核心功能，不能关闭", 400)
        FunctionCallingManager.set_manually_enabled(payload.name, payload.enabled)
        plugin = function_plugin_manager.get(owner)
        if plugin is None:
            return Result.fail(f"功能插件 {owner} 不存在", 404)
        return Result.ok(_describe(plugin), "设置成功!")
    except FunctionCallingEssentialError as e:
        return Result.fail(str(e), 400)
    except Exception as e:
        log.error(
            f"{router.prefix}/set_plugin_function_state 调用错误:{str_traceback(e)}"
        )
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/set_plugin_skill_state",
    dependencies=[authentication()],
    response_model=Result[FunctionPluginInfo],
    response_class=JSONResponse,
    description="设置功能插件内技能的启用开关（插件内只能开关，不能增删改）",
)
async def _(payload: SetFunctionPluginModel) -> Result[FunctionPluginInfo]:
    try:
        owner = function_plugin_manager.owner_of_skill(payload.name)
        if owner is None:
            return Result.fail(f"技能 {payload.name} 不属于任何功能插件", 404)
        skill_manager.set_enabled(payload.name, payload.enabled)
        plugin = function_plugin_manager.get(owner)
        if plugin is None:
            return Result.fail(f"功能插件 {owner} 不存在", 404)
        return Result.ok(_describe(plugin), "设置成功!")
    except ValueError as e:
        return Result.fail(str(e), 404)
    except Exception as e:
        log.error(f"{router.prefix}/set_plugin_skill_state 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")
