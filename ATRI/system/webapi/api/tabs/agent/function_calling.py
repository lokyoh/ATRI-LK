from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ATRI.exceptions import str_traceback
from ATRI.log import log
from ATRI.system.agent.agent.function_calling import FunctionCallingManager

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


class FunctionCallingInfo(BaseModel):
    name: str
    """功能名（function_name，开关状态的持久化键）"""
    description: str
    """功能说明（会进入功能提示词）"""
    args: list[FunctionCallingArgInfo]
    """参数列表"""
    manually_enabled: bool
    """手动开关状态"""
    can_enable: bool
    """当前环境是否支持该功能"""
    can_enable_reason: str
    """环境不可用原因（available 为真时为空串）"""


class FunctionCallingState(BaseModel):
    name: str
    manually_enabled: bool


class SetFunctionCallingModel(BaseModel):
    name: str
    enabled: bool


def _describe(name: str, func: type) -> FunctionCallingInfo:
    check = FunctionCallingManager.get_can_enable(name)
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
        can_enable=check.available,
        can_enable_reason=check.reason,
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
        if payload.name not in FunctionCallingManager.function_calling:
            return Result.fail(f"功能 {payload.name} 不存在", 404)
        FunctionCallingManager.set_manually_enabled(payload.name, payload.enabled)
        return Result.ok(
            FunctionCallingState(
                name=payload.name,
                manually_enabled=FunctionCallingManager.is_manually_enabled(
                    payload.name
                ),
            ),
            "设置成功!",
        )
    except Exception as e:
        log.error(f"{router.prefix}/set_function_calling 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")
