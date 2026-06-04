import json
import random
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .contents import LLMContents
from ..utils import log, request

NO_MODEL_ERR = "没有可用的模型"
ALL_MODEL_RESP_ERR = "没有可用的模型"


class ModelType(Enum):
    """预定义的模型类型常量"""

    CHAT = "chat"  # 聊天模型
    IMAGE = "image"  # 图片处理模型
    TOOL = "tool"  # 工具模型
    REASONING = "reasoning"  # 思考模型
    ACTION = "action"  # 实现模型
    LANGUAGE = "language"  # 语言处理模型
    EMBEDDING = "embedding"  # 嵌入模型


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
            api_key: Optional[str] = None,
            headers: Optional[Dict[str, str]] = None,
            method: str = "POST",
            call_func: Optional[Callable[..., Awaitable[Any]]] = None,
    ):
        """
        :param name: 模型唯一标识名称
        :param model: 模型名
        :param endpoint: API 端点 URL
        :param api_key: 可选的 API 密钥，将自动添加到 Authorization 头
        :param headers: 额外的 HTTP 头
        :param method: HTTP 方法，默认为 POST
        :param call_func: 自定义调用函数，若提供则完全接管调用逻辑
                          函数签名应为 async func(model: Model, **kwargs) -> Any
        """
        self.name = name
        self.model = model
        self.endpoint = endpoint
        self.api_key = api_key
        self.temperature = temperature
        self.headers = headers or {}
        self.method = method.upper()
        self.call_func = call_func

    async def call(self, content: str | LLMContents | list) -> Any:
        """
        调用模型，返回解析后的响应（通常为 JSON 对象）
        若提供了 call_func，则使用自定义逻辑；否则使用默认的 HTTP 请求。
        """
        if self.call_func:
            return await self.call_func(self, content)
        if isinstance(content, LLMContents):
            _content = []
            for part in content.contents:
                if part.type == "text":
                    _content.append({"type": "text", "text": part.content})
                elif part.type == "image":
                    _content.append({"type": "image_url", "image_url": {"url": part.content, "detail": "high"}})
                elif part.type == "audio":
                    _content.append({"type": "audio_url", "audio_url": {"url": part.content}})
            content = _content
        if self.method == "POST":
            headers = self.headers.copy()
            if self.api_key:
                headers.setdefault("Authorization", f"Bearer {self.api_key}")
                headers.setdefault("Content-Type", "application/json")

            data = {
                "model": self.model,
                "messages": [
                    {"role": "user", "content": content},
                ],
                "temperature": self.temperature
            }
            resp = await request.post(
                url=self.endpoint + "/chat/completions", json=data, headers=headers
            )
        else:
            raise ValueError(f"不支持的 HTTP 方法: {self.method}")

        try:
            resp_json = resp.json()
            _resp = {
                "usage": resp_json.get("usage", {}),
                "content": resp_json.get("choices", [{}])[0].get("message", {}).get("content", "").lstrip('\n')
            }
            log.debug(f"模型输出: {_resp.get('content')}\nTokens: {resp.get('usage')}")
            return _resp
        except Exception:
            try:
                resp_jons = json.loads(resp.text)
                _resp = {
                    "usage": resp_jons.get("usage", {}),
                    "content": resp_jons.get("choices", [{}])[0].get("message", {}).get("content", "").lstrip('\n')
                }
                log.debug(f"模型输出: {_resp.get('content')}\nTokens: {resp_jons.get('usage')}")
                return _resp
            except Exception:
                log.debug(f"模型输出: {resp.text}")
                return resp.text


class LLMManager:
    """
    模型管理器，负责注册和调用模型
    """

    def __init__(self):
        self._models: Dict[str, LLMModel] = {}
        self._typed_models: Dict[str, List[str]] = {}

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

    def get_model(self, name: str) -> Optional[LLMModel]:
        """
        根据名称获取单个模型
        """
        return self._models.get(name)

    def get_models(self, model_type: str) -> List[LLMModel]:
        """
        根据类型获取所有已注册的模型列表
        :param model_type: 模型类型，如 ModelType.CHAT
        :return: 该类型的所有模型实例列表
        """
        return [self._models[m] for m in self._typed_models[model_type]]

    async def call_model(self, name: str, content: str | LLMContents) -> Any:
        """
        调用指定名称的模型，传入调用参数
        :param name: 模型名称
        :param content: 调用内容，根据模型 API 要求提供
        :return: 模型响应（通常为 JSON 对象）
        """
        model = self.get_model(name)
        if not model:
            raise ValueError(f"模型 '{name}' 未注册")
        log.debug(f"调用模型 {name}")
        resp = await model.call(content)
        return resp

    async def call_model_by_type(self, model_type: ModelType, content: str | LLMContents) -> dict | str:
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
            return NO_MODEL_ERR
        for model in random.sample(models, len(models)):
            model: LLMModel
            times = 0
            while True:
                try:
                    log.debug(f"调用模型 {model.name} (类型: {model_type})")
                    resp = await model.call(content)
                    return resp
                except Exception as e:
                    log.warning(f"调用模型 {model.name} 失败: {e}")
                times += 1
                if times >= 3:
                    break
        raise ALL_MODEL_RESP_ERR

    def clear_all(self):
        self._models.clear()
        self._typed_models.clear()

    def has_type(self, model_type: ModelType) -> bool:
        return model_type.value in self._typed_models
