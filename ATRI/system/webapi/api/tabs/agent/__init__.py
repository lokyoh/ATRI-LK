from typing import List

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ATRI.exceptions import str_traceback
from ATRI.log import log
from ATRI.system.agent.config import AgentConfig, config_manager
from ATRI.system.agent.llm.provider import LLMModel, LLMProvider, ProviderManager

from ....base_model import Result
from ....utils import authentication

router = APIRouter(prefix="/agent")


@router.get(
    "/get_settings",
    dependencies=[authentication()],
    response_model=Result[AgentConfig],
    response_class=JSONResponse,
    description="获取agent插件配置",
)
async def _() -> Result[AgentConfig]:
    try:
        return Result.ok(config_manager.config(), "拿到信息啦!")
    except Exception as e:
        log.error(f"{router.prefix}/get_settings 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/set_settings",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="设置agent插件配置",
)
async def _(settings: AgentConfig) -> Result:
    try:
        config_manager.change_config(settings)
        return Result.ok(info="设置成功!")
    except Exception as e:
        log.error(f"{router.prefix}/set_settings 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.get(
    "/get_providers",
    dependencies=[authentication()],
    response_model=Result[List[LLMProvider]],
    response_class=JSONResponse,
    description="获取所有的llm provider",
)
async def _() -> Result[List[LLMProvider]]:
    try:
        return Result.ok(ProviderManager.get_provider_list(), "拿到信息啦!")
    except Exception as e:
        log.error(f"{router.prefix}/get_providers 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/add_provider",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="添加一个llm provider",
)
async def _(provider: LLMProvider) -> Result:
    try:
        ProviderManager.register(provider)
        ProviderManager.save_provider_config()
        return Result.ok(info="添加成功!")
    except Exception as e:
        log.error(f"{router.prefix}/add_provider 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/del_provider",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="删除一个llm provider",
)
async def _(provider_name: str) -> Result:
    try:
        ProviderManager.del_provider(provider_name)
        ProviderManager.save_provider_config()
        return Result.ok(info="删除成功!")
    except Exception as e:
        log.error(f"{router.prefix}/del_provider 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/set_provider",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="设置一个llm provider",
)
async def _(provider_name, provider: LLMProvider) -> Result:
    try:
        ProviderManager.set_provider(provider_name, provider)
        return Result.ok(info="设置成功!")
    except Exception as e:
        log.error(f"{router.prefix}/set_provider 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/add_model",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="添加一个llm模型到指定的provider",
)
async def _(provider_name: str, model: LLMModel) -> Result:
    try:
        ProviderManager.add_model(provider_name, model)
        ProviderManager.save_provider_config()
        return Result.ok(info="添加成功!")
    except Exception as e:
        log.error(f"{router.prefix}/add_model 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/del_model",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="删除一个llm模型从指定的provider",
)
async def _(provider_name: str, model_name: str) -> Result:
    try:
        ProviderManager.del_model(provider_name, model_name)
        ProviderManager.save_provider_config()
        return Result.ok(info="删除成功!")
    except Exception as e:
        log.error(f"{router.prefix}/del_model 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")
