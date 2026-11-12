from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ATRI.exceptions import str_traceback
from ATRI.log import log
from ATRI.system.agent.agent.function_calling import FunctionCallingManager
from ATRI.system.agent.agent.function_permission import (
    PermissionContext,
    PermissionLevel,
    PermissionResolver,
)
from ATRI.system.agent.agent.function_plugin.manage import function_plugin_manager

from ....base_model import Result
from ....utils import authentication

# 前缀由所属的 agent 包级 router（prefix="/agent"）提供：
# FastAPI 的 include_router 会拼接前缀，子模块再写 prefix="/agent" 会变成 /agent/agent。
router = APIRouter()


class FunctionCallingArgInfo(BaseModel):
    name: str
    """参数名"""
    type: str
    """参数类型"""
    description: str
    """参数说明"""


class PermissionDecisionInfo(BaseModel):
    allowed: bool
    """该等级下是否允许"""
    reason: str
    """允许 / 拒绝的具体原因"""
    scope: str
    """本次求值使用的作用域"""
    action: str = ""
    """动作名（scope_by_action 的键）"""
    chain: list[str] = []
    """求值链路，供 explain 展示"""


class FunctionCallingInfo(BaseModel):
    name: str
    """功能名（function_name，开关状态的持久化键）"""
    description: str
    """功能说明（会进入功能提示词）"""
    args: list[FunctionCallingArgInfo]
    """参数列表"""
    manually_enabled: bool
    """手动开关状态"""
    always_enabled: bool
    """核心功能（say 等）：开关恒为开，不可关闭"""
    can_enable: bool
    """当前环境是否支持该功能"""
    can_enable_reason: str
    """环境不可用原因（available 为真时为空串）"""
    plugin: str | None = None
    """所属功能插件名；非空时该功能在功能插件页管理，不在本页展示"""
    scope: str = "bot"
    """默认作用域：chat / group / bot"""
    scope_by_action: dict[str, str] = {}
    """按动作细分的作用域"""
    aliases: list[str] = []
    """旧名 / 别名；解析时归一到 ``name``，配置与提示词只用规范名"""
    returns_data: bool = True
    """调用结果是否需要回填给思考（False = 副作用调用）"""
    prompt_hidden: bool = False
    """不进入常规功能提示词，但仍可被模型直接调用"""
    permission: dict[str, PermissionDecisionInfo] = {}
    """三个等级对该功能的默认求值结果"""


class FunctionCallingState(BaseModel):
    name: str
    manually_enabled: bool


class SetFunctionCallingModel(BaseModel):
    name: str
    enabled: bool


def _permission_map(name: str) -> dict[str, PermissionDecisionInfo]:
    """三个等级对该功能的规则层求值（不含环境与对话人检查）。"""
    result: dict[str, PermissionDecisionInfo] = {}
    for level in (PermissionLevel.MASTER, PermissionLevel.ADMIN, PermissionLevel.USER):
        decision = PermissionResolver.check(
            name, PermissionContext(level=level), record=False
        )
        result[level.value] = PermissionDecisionInfo(
            allowed=decision.allowed,
            reason=decision.reason,
            scope=decision.scope.value,
            action=decision.action,
            chain=list(decision.chain),
        )
    return result


def _describe(name: str, func: type) -> FunctionCallingInfo:
    check = FunctionCallingManager.get_can_enable(name)
    scope = getattr(func, "scope", None)
    scope_value = getattr(scope, "value", str(scope or "bot"))
    scope_by_action = {
        str(key): getattr(value, "value", str(value))
        for key, value in (getattr(func, "scope_by_action", None) or {}).items()
    }
    return FunctionCallingInfo(
        name=name,
        description=func.description,
        args=[
            FunctionCallingArgInfo(
                name=arg.name, type=arg.type, description=arg.description
            )
            for arg in func.args
        ],
        manually_enabled=FunctionCallingManager.is_manually_enabled(name),
        always_enabled=FunctionCallingManager.is_essential(name),
        can_enable=check.available,
        can_enable_reason=check.reason,
        plugin=function_plugin_manager.owner_of_function(name),
        scope=scope_value,
        scope_by_action=scope_by_action,
        aliases=list(getattr(func, "aliases", ()) or ()),
        returns_data=bool(getattr(func, "returns_data", True)),
        prompt_hidden=bool(getattr(func, "prompt_hidden", False)),
        permission=_permission_map(name),
    )


@router.get(
    "/get_function_callings",
    dependencies=[authentication()],
    response_model=Result[list[FunctionCallingInfo]],
    response_class=JSONResponse,
    description="获取已注册的功能调用与开关状态",
)
async def _() -> Result[list[FunctionCallingInfo]]:
    try:
        # 只读注册表：不调用 register_function_calling()，避免 clear_all() 影响外部插件注册
        return Result.ok(
            [
                _describe(name, func)
                for name, func in FunctionCallingManager.function_calling.items()
            ],
            "拿到信息啦!",
        )
    except Exception as e:
        log.error(f"{router.prefix}/get_function_callings 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/set_function_calling",
    dependencies=[authentication()],
    response_model=Result[FunctionCallingState],
    response_class=JSONResponse,
    description="设置单个功能调用的手动开关",
)
async def _(payload: SetFunctionCallingModel) -> Result[FunctionCallingState]:
    try:
        canonical = FunctionCallingManager.resolve_name(payload.name)
        if canonical not in FunctionCallingManager.function_calling:
            return Result.fail(f"功能 {payload.name} 不存在", 404)
        if not payload.enabled and FunctionCallingManager.is_essential(canonical):
            return Result.fail(f"功能 {canonical} 是核心功能，不能关闭", 400)
        owner = function_plugin_manager.owner_of_function(canonical)
        if owner:
            return Result.fail(
                f"功能 {canonical} 属于功能插件 {owner}，请到「功能插件」页开关",
                400,
            )
        FunctionCallingManager.set_manually_enabled(canonical, payload.enabled)
        return Result.ok(
            FunctionCallingState(
                name=canonical,
                manually_enabled=FunctionCallingManager.is_manually_enabled(canonical),
            ),
            "设置成功!",
        )
    except Exception as e:
        log.error(f"{router.prefix}/set_function_calling 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.get(
    "/explain_permission",
    dependencies=[authentication()],
    response_model=Result[PermissionDecisionInfo],
    response_class=JSONResponse,
    description="解释某个等级 / 用户为什么能用或不能用某个功能",
)
async def _(
    function: str,
    level: str = "user",
    user_id: str = "",
    group_id: str = "",
    action: str = "",
    is_private: bool = False,
) -> Result[PermissionDecisionInfo]:
    try:
        canonical = FunctionCallingManager.resolve_name(function)
        if canonical not in FunctionCallingManager.function_calling:
            return Result.fail(f"功能 {function} 不存在", 404)
        try:
            parsed = PermissionLevel(str(level).strip().lower())
        except ValueError:
            return Result.fail(f"未知等级：{level}", 400)
        ctx = PermissionContext(
            user_id=user_id,
            group_id=group_id,
            level=parsed,
            is_private=is_private,
        )
        decision = PermissionResolver.check(canonical, ctx, action, record=False)
        return Result.ok(
            PermissionDecisionInfo(
                allowed=decision.allowed,
                reason=decision.reason,
                scope=decision.scope.value,
                action=decision.action,
                chain=list(decision.chain),
            ),
            decision.explain(),
        )
    except Exception as e:
        log.error(f"{router.prefix}/explain_permission 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")
