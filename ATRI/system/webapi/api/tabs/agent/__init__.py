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
        agent_config: AgentConfig = config_manager.config().model_copy(deep=True)
        agent_config.embedding.api_key = ""
        agent_config.search.api_key = ""
        agent_config.tts.api_key = ""
        return Result.ok(agent_config, "拿到信息啦!")
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
        current_settings: AgentConfig = config_manager.config()
        settings.search.api_key = (
            settings.search.api_key or current_settings.search.api_key
        )
        settings.tts.api_key = settings.tts.api_key or current_settings.tts.api_key
        settings.embedding.api_key = (
            settings.embedding.api_key or current_settings.embedding.api_key
        )
        config_manager.change_config(settings)
        return Result.ok(info="设置成功!")
    except Exception as e:
        log.error(f"{router.prefix}/set_settings 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.get(
    "/get_providers",
    dependencies=[authentication()],
    response_model=Result[list[LLMProvider]],
    response_class=JSONResponse,
    description="获取所有的llm provider",
)
async def _() -> Result[list[LLMProvider]]:
    try:
        p_list = ProviderManager.get_provider_list()
        for p in p_list:
            p.api_key = ""
        return Result.ok(p_list, "拿到信息啦!")
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
        current_provider = ProviderManager.providers.get(provider_name)
        if current_provider and not provider.api_key:
            provider.api_key = current_provider.api_key
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
