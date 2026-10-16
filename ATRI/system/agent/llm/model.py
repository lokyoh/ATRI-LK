import json
import random
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any

import httpx

from ATRI.exceptions import str_traceback

from ..utils import log, request
from .contents import LLMContents, LLMMessages
from .token import MTYM, TokenUsage

NO_MODEL_ERR = "没有可用的模型"
ALL_MODEL_RESP_ERR = "没有可用的模型"


class ModelType(Enum):
    """预定义的模型类型常量"""

    CHAT = "chat"  # 聊天模型
    IMAGE = "image"  # 图片处理模型
    TOOL = "tool"  # 工具模型
    EMBEDDING = "embedding"  # 嵌入模型
    TTS = "tts"  # 文本转语音模型
    SYSTEMONE = "sys1"  # SystemOne模型


class LLMResponse:
    def __init__(self, content: Any, usage: dict[str, Any] | None = None):
        self.content = content
        self.usage = usage


class ModelRequestError(Exception):
    """模型调用错误异常"""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class LLMModel:
    """
    单个模型的描述与调用封装
    """

    def __init__(
        self,
        name: str,
        model: str,
        endpoint: str,
        temperature: float = 1,
        api_key: str | None = None,
        headers: dict[str, str] | None = None,
        method: str = "POST",
        call_func: Callable[..., Awaitable[Any]] | None = None,
        data: dict[str, Any] | None = None,
    ):
        """
        :param name: 模型唯一标识名称
        :param model: 模型名
        :param endpoint: API 端点 URL
        :param api_key: 可选的 API 密钥,将自动添加到 Authorization 头
        :param headers: 额外的 HTTP 头
        :param method: HTTP 方法，默认为 POST
        :param call_func: 自定义调用函数，若提供则完全接管调用逻辑
                          函数签名应为 async func(model: Model, **kwargs) -> Any
        """
        self.name: str = name
        self.model: str = model
        self.endpoint: str = endpoint
        self.api_key: str | None = api_key
        self.temperature: float = temperature
        self.headers: dict[str, str] | None = headers or {}
        self.method: str = method.upper()
        self.call_func: Callable[..., Awaitable[Any]] | None = call_func
        self.data: dict[str, Any] = data or {}

    async def call(self, content: str | LLMContents | list | Any) -> LLMResponse:
        """
        调用模型，返回解析后的响应（通常为 JSON 对象）
        若提供了 call_func，则使用自定义逻辑；否则使用默认的 HTTP 请求。
        """
        if self.call_func:
            return await self.call_func(self, content)
        if isinstance(content, LLMContents):
            log_content = content.get_shorten_content()
            messages = [
                {"role": "user", "content": content.get_contents()},
            ]
        elif isinstance(content, LLMMessages):
            log_content = content.get_shorten_content()
            messages = [
                {"role": msg.role, "content": msg.content.get_contents()}
                for msg in content.messages
            ]
        else:
            log_content = content
            messages = [
                {"role": "user", "content": content},
            ]
        if self.method == "POST":
            headers = self.headers.copy()
            if self.api_key:
                headers.setdefault("Authorization", f"Bearer {self.api_key}")
                headers.setdefault("Content-Type", "application/json")
            data = {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
                **self.data,
            }
            log.debug(f"模型输入: {log_content}")
            resp = await request.post(
                url=self.endpoint + "/chat/completions", json=data, headers=headers
            )
        else:
            raise ValueError(f"不支持的 HTTP 方法: {self.method}")
        try:
            resp_json = resp.json()
            resp_content = (
                resp_json.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .lstrip("\n")
            )
            usage_dict = resp_json.get("usage", {})
            usage = TokenUsage(
                prompt_tokens=usage_dict.get("prompt_tokens", 0),
                output_tokens=usage_dict.get("completion_tokens", 0),
                cache_hit_tokens=usage_dict.get("prompt_cache_hit_tokens", 0),
            )
            log.debug(f"模型输出: {resp_content}\nTokens: {usage}")
            return LLMResponse(content=resp_content, usage=usage)
        except Exception:
            try:
                resp_json = json.loads(resp.text)
                resp_content = (
                    resp_json.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .lstrip("\n")
                )
                usage_dict = resp_json.get("usage", {})
                usage = TokenUsage(
                    prompt_tokens=usage_dict.get("prompt_tokens", 0),
                    output_tokens=usage_dict.get("completion_tokens", 0),
                    cache_hit_tokens=usage_dict.get("prompt_cache_hit_tokens", 0),
                )
                log.debug(f"模型输出: {resp_content}\nTokens: {usage}")
                return LLMResponse(content=resp_content, usage=usage)
            except Exception as e:
                log.debug(f"模型输出: {resp.text}")
                raise ModelRequestError(
                    f"请求失败了: {resp.text}", status_code=resp.status_code
                ) from e


class LLMManager:
    """
    模型管理器，负责注册和调用模型
    """

    def __init__(self):
        self._models: dict[str, LLMModel] = {}
        self._typed_models: dict[str, list[str]] = {}

    def register_model(self, model: LLMModel, model_type: str) -> None:
        """
        注册一个模型，若名称已存在则覆盖并记录警告
        """
        if model.name in self._models:
            log.warning(f"模型 {model.name} 已存在，将被覆盖")
        self._models[model.name] = model
        if model_type not in self._typed_models:
            self._typed_models[model_type] = []
        self._typed_models[model_type].append(model.name)
        log.info(f"已注册模型 {model.name} (类型: {model_type})")

    def get_model(self, name: str) -> LLMModel | None:
        """
        根据名称获取单个模型
        """
        return self._models.get(name)

    def get_models(self, model_type: str) -> list[LLMModel]:
        """
        根据类型获取所有已注册的模型列表
        :param model_type: 模型类型，如 ModelType.CHAT
        :return: 该类型的所有模型实例列表
        """
        return [self._models[m] for m in self._typed_models[model_type]]

    def get_model_type(self, model: str) -> str:
        for m_type, models in self._typed_models.items():
            if model in models:
                return m_type
        return "未知模型"

    async def call_model(
        self,
        model: str | LLMModel,
        content: str | LLMContents | list | Any,
        event: str = "未知调度",
    ) -> LLMResponse:
        """
        调用指定名称的模型，传入调用参数
        :param name: 模型名称
        :param content: 调用内容，根据模型 API 要求提供
        :return: 模型响应（通常为 JSON 对象）
        """
        if isinstance(model, str):
            m_name = model
            model = self.get_model(m_name)
            if not model:
                raise ValueError(f"模型 '{m_name}' 未注册")
        else:
            m_name = model.name
        try:
            log.debug(f"调用模型 {m_name} (类型: {self.get_model_type(m_name)})")
            resp: LLMResponse = await model.call(content)
            usage = resp.usage
            if isinstance(usage, TokenUsage):
                MTYM.add_usage(m_name, event, usage)
            return resp
        except ModelRequestError as e:
            log.warning(
                f"调用模型 {model.name} 失败,错误码: {e.status_code},信息: {e.args}"
            )
            raise
        except httpx.ConnectError as e:
            err_str = str(e)
            if "name resolution" in err_str or "Name or service not known" in err_str:
                log.warning(
                    f"调用模型 {model.name} 失败:DNS 解析失败，请检查网络或域名"
                )
            else:
                log.warning(f"调用模型 {model.name} 失败:其他连接错误: {e}")
            raise
        except httpx.TimeoutException:
            log.warning(f"调用模型 {model.name} 失败:请求超时")
            raise
        except httpx.HTTPError:
            log.warning(f"调用模型 {model.name} 失败:HTTP 错误")
            raise
        except Exception as e:
            log.warning(f"调用模型 {model.name} 失败: {str_traceback(e)}")
            raise

    async def call_model_by_type(
        self,
        model_type: ModelType,
        content: str | LLMContents | list | Any,
        event: str = "未知调度",
    ) -> LLMResponse:
        """
        调用指定类型的模型，返回每个模型的响应列表
        :param model_type: 模型类型
        :param content: 传递给每个模型的调用内容
        :return: 所有模型响应的列表（顺序与 get_models 返回的顺序一致）
        """
        model_type: str = model_type.value
        models = self.get_models(model_type)
        if not models:
            log.warning(f"没有找到类型为 '{model_type}' 的模型")
            raise ModelRequestError(NO_MODEL_ERR)
        for model in random.sample(models, len(models)):
            times = 0
            while True:
                try:
                    return await self.call_model(model, content, event)
                except Exception:
                    pass
                times += 1
                if times >= 3:
                    break
        raise ModelRequestError(ALL_MODEL_RESP_ERR)

    def clear_all(self):
        self._models.clear()
        self._typed_models.clear()

    def has_type(self, model_type: ModelType) -> bool:
        return model_type.value in self._typed_models
