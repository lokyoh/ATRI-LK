from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ATRI.configs import Field
from ATRI.exceptions import str_traceback
from ATRI.log import log
from ATRI.system.agent.config import config
from ATRI.system.agent.llm import (
    LLMModel,
    LLMProvider,
    ModelPricing,
    ModelType,
    ProviderManager,
    Sys1Content,
    Sys1Question,
    provider_type,
)
from ATRI.system.agent.llm.provider import PRICING_UNIT_TOKENS
from ATRI.system.agent.llm.token import MTYM, TokenUsageData

from ....base_model import Result
from ....utils import authentication
from .function_calling import router as function_calling_router
from .skills import router as skills_router

router = APIRouter(prefix="/agent")
router.include_router(function_calling_router)
router.include_router(skills_router)


class ModelPricingInfo(BaseModel):
    """模型计费信息（前端据此换算费用）"""

    pricing: ModelPricing
    """三项单价，元/1M tokens"""
    unit_tokens: int = PRICING_UNIT_TOKENS
    """单价的计量基数"""


def _get_pricing_map() -> dict[str, ModelPricingInfo]:
    """汇总所有 provider 下模型的计费配置，以模型 name 为键。

    模型 name 要求全局唯一；若配置中出现重名，按 provider 注册顺序取第一个
    配置了计费的条目，保证结果稳定。未配置计费的模型不会出现在结果里。
    """
    pricing_map: dict[str, ModelPricingInfo] = {}
    for provider in ProviderManager.providers.values():
        for model in provider.models:
            if not model.name or model.pricing is None:
                continue
            pricing_map.setdefault(model.name, ModelPricingInfo(pricing=model.pricing))
    return pricing_map


def _flatten_config_updates(
    values: dict, fields: dict[str, Field], prefix: str = ""
) -> dict[str, object]:
    updates: dict[str, object] = {}
    for key, value in values.items():
        field = fields.get(key)
        if field is None:
            raise ValueError(f"未知的配置字段: {prefix}{key}")
        if not field.editable:
            raise ValueError(f"配置字段不可编辑: {prefix}{key}")

        field_path = f"{prefix}{key}"
        if field.children:
            if not isinstance(value, dict):
                raise ValueError(f"配置字段必须是对象: {field_path}")
            updates.update(
                _flatten_config_updates(
                    value,
                    field.children,
                    f"{field_path}.",
                )
            )
            continue

        expected_type = field.type_
        if expected_type is bool:
            valid_type = isinstance(value, bool)
        elif expected_type is int:
            valid_type = isinstance(value, int) and not isinstance(value, bool)
        elif expected_type is float:
            valid_type = isinstance(value, (int, float)) and not isinstance(value, bool)
        else:
            valid_type = isinstance(value, expected_type)
        if not valid_type:
            raise ValueError(f"配置字段类型不正确: {field_path}")

        if (
            field.item_type
            and isinstance(value, list)
            and not all(isinstance(item, field.item_type) for item in value)
        ):
            raise ValueError(f"配置列表项类型不正确: {field_path}")

        if field_path.endswith(".api_key") and not value:
            continue
        updates[field_path] = value
    return updates


@router.get(
    "/get_settings",
    dependencies=[authentication()],
    response_model=Result[dict],
    response_class=JSONResponse,
    description="获取agent插件配置",
)
async def _() -> Result[dict]:
    try:
        config_schema = config.describe()

        def redact_api_keys(fields: list[dict]) -> None:
            for field in fields:
                if field["key"] == "api_key":
                    field["value"] = ""
                if children := field.get("children"):
                    redact_api_keys(children)

        redact_api_keys(config_schema["fields"])
        return Result.ok(config_schema, "拿到信息啦!")
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
async def _(settings: dict[str, object]) -> Result:
    try:
        updates = _flatten_config_updates(
            settings,
            type(config)._fields,
        )
        config.update(updates)
        return Result.ok(info="设置成功!")
    except ValueError as e:
        return Result.fail(str(e))
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
        ProviderManager.save_provider_config()
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


@router.post(
    "/test_model",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="测试指定模型的 API 连通性",
)
async def _(provider_name: str, model_name: str) -> Result:
    provider = ProviderManager.providers.get(provider_name)
    if not provider:
        return Result.fail(f"未找到 Provider: {provider_name}", code=404)
    model_config = next(
        (model for model in provider.models if model.name == model_name), None
    )
    if not model_config:
        return Result.fail(f"未找到模型: {model_name}", code=404)
    model_factory = provider_type.get(provider.provider_type)
    if not model_factory:
        return Result.fail(
            f"暂不支持的 Provider 类型: {provider.provider_type}", code=400
        )
    test_model = None
    response = None
    test_content: str | Sys1Content = "请仅回复 OK。"
    if provider.provider_type == "sys1":
        test_content = Sys1Content(
            state="ATRI 模型 API 连通性测试",
            questions={
                "connectivity": Sys1Question(
                    type="noul",
                    instructions="确认请求成功，并简短回复 OK。",
                )
            },
        )
    elif provider.provider_type == "embedding":
        test_content = "ATRI model API connectivity test"
    try:
        test_model = model_factory(
            name=model_config.name,
            model=model_config.model,
            endpoint=provider.url,
            api_key=provider.api_key,
            temperature=model_config.temperature,
            data=model_config.data,
        )
        response = await test_model.call(test_content)
        if response.content is None or response.content == "" or response.content == []:
            return Result.fail("API 请求成功，但模型未返回有效内容")
        return Result.ok(info=f"API 测试成功：{provider_name}/{model_name}")
    except Exception as e:
        log.error(f"{router.prefix}/test_model 调用错误:{str_traceback(e)}")
        return Result.fail(f"API 测试失败: {e}")
    finally:
        if provider.provider_type == "tts":
            output_path = getattr(response, "content", None)
            if isinstance(output_path, Path):
                try:
                    output_path.unlink(missing_ok=True)
                except OSError as e:
                    log.warning(f"删除 API 测试临时音频失败:{e}")


@router.post(
    "/get_provider_type",
    dependencies=[authentication()],
    response_model=Result[list[str]],
    response_class=JSONResponse,
    description="获取注册的模型供应商类型",
)
async def _() -> Result[list[str]]:
    p_type = list(provider_type)
    return Result.ok(info="拿到信息啦!", data=p_type)


@router.post(
    "/get_model_type",
    dependencies=[authentication()],
    response_model=Result[list[str]],
    response_class=JSONResponse,
    description="获取注册的模型类型",
)
async def _() -> Result[list[str]]:
    m_type = [type_.value for type_ in ModelType]
    return Result.ok(info="拿到信息啦!", data=m_type)


@router.get(
    "/get_token_usage",
    dependencies=[authentication()],
    response_model=Result[dict[str, list[TokenUsageData]]],
    response_class=JSONResponse,
    description="获取模型 Token 用量记录",
)
async def _() -> Result[dict[str, list[TokenUsageData]]]:
    try:
        return Result.ok(data=MTYM.get_all_usage(), info="拿到信息啦!")
    except Exception as e:
        log.error(f"{router.prefix}/get_token_usage 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.get(
    "/get_model_pricing",
    dependencies=[authentication()],
    response_model=Result[dict[str, ModelPricingInfo]],
    response_class=JSONResponse,
    description="获取各模型的计费配置（元/1M tokens），未配置计费的模型不会出现在结果中",
)
async def _() -> Result[dict[str, ModelPricingInfo]]:
    try:
        return Result.ok(data=_get_pricing_map(), info="拿到信息啦!")
    except Exception as e:
        log.error(f"{router.prefix}/get_model_pricing 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")
