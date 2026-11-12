import copy
import json
import random
import time
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any

import httpx

from ATRI.exceptions import str_traceback

from ..utils import log, request
from .contents import LLMContents, LLMMessages
from .log import log_llm_call
from .token import MTYM, TokenUsage

NO_MODEL_ERR = "没有可用的模型"
ALL_MODEL_RESP_ERR = "所有模型请求失败"


class ModelType(Enum):
    """预定义的模型类型常量。

    前缀 ``atri-`` 的类型只给 ATRIAgent 使用；其余通用类型继续供
    ``lkpet`` / ``trpg`` / ``feed`` 等非 Agent 服务使用。Agent **只认
    atri-*，绝不回退到通用类型**。
    """

    ATRI_THINK = "atri-think"
    """Agent 思考：读上下文、选工具、产出说话意图"""
    ATRI_CHAT = "atri-chat"
    """Agent 聊天：把意图变成自然文本 / 卡片 / 表情"""
    ATRI_JUDGE = "atri-judge"
    """Agent 主动聊天判断"""
    ATRI_MEMORY = "atri-memory"
    """Agent 记忆抽取与总结"""
    ATRI_PROFILE = "atri-profile"
    """Agent 用户画像生成与修改"""
    ATRI_SCHEDULE = "atri-schedule"
    """Agent 填入式日程生成"""
    ATRI_OUTFIT = "atri-outfit"
    """Agent 白天 / 晚上着装生成"""
    ATRI_HOME = "atri-home"
    """Agent 家的内容生成与扩写"""
    ATRI_SUMMARY = "atri-summary"
    """Agent 转发消息 / 长文总结"""
    ATRI_VISION = "atri-vision"
    """Agent 图片理解"""
    ATRI_EMBEDDING = "atri-embedding"
    """Agent 记忆检索向量"""
    ATRI_TTS = "atri-tts"
    """Agent 语音合成"""
    CHAT = "chat"
    """聊天通用模型"""
    WORK = "work"
    """工作通用模型"""
    TOOL = "tool"
    """小型工具通用模型"""
    IMAGE = "image"
    """图片处理模型"""
    EMBEDDING = "embedding"
    """嵌入模型"""
    TTS = "tts"
    """文本转语音模型"""
    SYSTEMONE = "sys1"
    """SystemOne模型"""


class LLMResponse:
    def __init__(
        self,
        content: Any,
        usage: dict[str, Any] | None = None,
        model: str | None = None,
    ):
        self.content = content
        self.usage = usage
        #: 实际应答的模型名（由 LLMManager 回填，供调用方换算计费）
        self.model = model


class ModelRequestError(Exception):
    """模型调用错误异常"""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def normalize_api_keys(api_key: str | list[str] | None) -> list[str]:
    """把 API key 统一成列表；空值表示无需鉴权。"""
    if not api_key:
        return []
    if isinstance(api_key, str):
        return [api_key]
    return [key for key in api_key if key]


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
        api_key: str | list[str] | None = None,
        headers: dict[str, str] | None = None,
        method: str = "POST",
        call_func: Callable[..., Awaitable[Any]] | None = None,
        data: dict[str, Any] | None = None,
    ):
        """
        :param name: 模型唯一标识名称
        :param model: 模型名
        :param endpoint: API 端点 URL（仅支持单个）
        :param api_key: 可选的 API 密钥；可以是单个密钥或按顺序尝试的密钥列表
        :param headers: 额外的 HTTP 头
        :param method: HTTP 方法，默认为 POST
        :param call_func: 自定义调用函数，若提供则完全接管调用逻辑
                          函数签名应为 async func(model: Model, **kwargs) -> Any
        """
        self.name: str = name
        self.model: str = model
        self.endpoint: str = endpoint
        self.api_keys: list[str] = normalize_api_keys(api_key)
        self.api_key: str | None = self.api_keys[0] if self.api_keys else None
        self.temperature: float = temperature
        self.headers: dict[str, str] | None = headers or {}
        self.method: str = method.upper()
        self.call_func: Callable[..., Awaitable[Any]] | None = call_func
        self.data: dict[str, Any] = data or {}

    async def call(self, content: str | LLMContents | list | Any) -> LLMResponse:
        """
        调用模型，按顺序尝试配置的多个 API 密钥，前一个失败后自动切换。

        每次尝试都在模型副本上执行，避免并发调用时互相篡改 ``api_key``。
        全部失败时抛出最后一次异常，保留原始状态码供上层识别。
        """
        key_total = len(self.api_keys)
        attempts = self.api_keys or [None]
        errors: list[Exception] = []
        for index, api_key in enumerate(attempts):
            attempt = copy.copy(self)
            attempt.api_key = api_key
            try:
                return await attempt._call_once(content)
            except Exception as exc:
                errors.append(exc)
                if index < len(attempts) - 1:
                    log.warning(
                        f"模型 {self.name} 通过 API {self.endpoint}"
                        f"（密钥 {index + 1}/{key_total}）调用失败，"
                        f"尝试下一个密钥：{exc}"
                    )
        if len(errors) == 1:
            raise errors[0]
        raise ModelRequestError(f"所有 API 密钥请求失败：{errors[-1]}") from errors[-1]

    async def _call_once(self, content: str | LLMContents | list | Any) -> LLMResponse:
        """使用当前 ``endpoint`` / ``api_key`` 发起一次调用。"""
        if self.call_func:
            return await self.call_func(self, content)
        if isinstance(content, LLMContents):
            messages = [
                {"role": "user", "content": content.get_contents()},
            ]
        elif isinstance(content, LLMMessages):
            messages = [
                {"role": msg.role, "content": msg.content.get_contents()}
                for msg in content.messages
            ]
        else:
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
                return LLMResponse(content=resp_content, usage=usage)
            except Exception as e:
                raise ModelRequestError(
                    f"请求失败了: {resp.text}", status_code=resp.status_code
                ) from e


AGENT_TYPES: frozenset[str] = frozenset(
    mt.value for mt in ModelType if mt.value.startswith("atri-")
)

DETERMINISTIC_TYPES: frozenset[str] = frozenset(
    {ModelType.EMBEDDING.value, ModelType.ATRI_EMBEDDING.value}
)


class AgentModelMissingError(ModelRequestError):
    """Agent 必需模型类型缺失。

    与「所有模型请求失败」区分开：这是配置问题，应该在入口就变成
    用户可见提示 + 主人告警，而不是让模型调用在深层抛一个笼统异常。
    """

    def __init__(self, model_type: ModelType, label: str = ""):
        self.model_type = model_type
        self.label = label or model_type.value
        super().__init__(f"未配置 {self.label}（{model_type.value}）模型")


class LLMManager:
    """
    模型管理器，负责注册和调用模型
    """

    def __init__(self):
        self._models: dict[str, LLMModel] = {}
        self._typed_models: dict[str, list[str]] = {}
        self._preferred: dict[str, str] = {}
        #: 已提示过「多模型未指定」的类型；这类告警只该出现一次，
        #: 否则每轮思考都会刷一条。
        self._multi_model_warned: set[str] = set()

    def set_preferred(self, model_type: str, model_name: str) -> bool:
        """指定某类型优先使用的模型。空串 = 清除指定、回到默认分配。

        :return: 指定的模型是否存在且确实注册了该类型。

        失败时**保留原值**，不改动。两种失败处理都是刻意的：

        - 从「指定 A」改成「写错的 B」：继续用 A，而不是退回随机。
          用户的意图明显是「换一个模型」，手误不该让行为退化成随机；
        - 从「未指定」改成「写错的 B」：仍然未指定，与原来一致。

        调用方（``config._apply_model_preferences``）必须把 False 变成一条
        告警——用户需要知道自己的配置没生效，否则会以为已经切换了。
        """
        type_key = str(model_type)
        name = (model_name or "").strip()
        if not name:
            self._preferred.pop(type_key, None)
            return True
        if name not in self._models:
            return False
        if name not in (self._typed_models.get(type_key) or []):
            return False
        self._preferred[type_key] = name
        return True

    def get_preferred(self, model_type: str) -> str:
        return self._preferred.get(str(model_type), "")

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
        """根据类型获取所有已注册的模型列表。

        未注册任何该类型的模型时返回**空列表**，而不是抛 ``KeyError``。

        这一点很重要：原先用 ``self._typed_models[model_type]`` 直接下标，
        类型没注册就抛 ``KeyError``；而 ``call_model_by_type`` 里那句
        ``if not models: raise ModelRequestError(NO_MODEL_ERR)`` 因此**永远走不到**。
        结果是「没配模型」抛的是一个未预期的 KeyError，所有
        ``except ModelRequestError`` 的降级分支全部失效——本该优雅跳过的功能
        变成直接冒泡。
        """
        names = self._typed_models.get(model_type)
        if not names:
            return []
        return [self._models[m] for m in names if m in self._models]

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
        model_type = self.get_model_type(m_name)
        try:
            started = time.perf_counter()
            try:
                resp: LLMResponse = await model.call(content)
            except BaseException as exc:
                duration = time.perf_counter() - started
                log_llm_call(
                    model=model,
                    model_type=model_type,
                    content=content,
                    event=event,
                    duration=duration,
                    error=exc,
                )
                log.debug(
                    f"模型调用 {m_name} (类型: {model_type}) | 事件={event} | "
                    f"耗时={duration:.3f}s | 失败: {type(exc).__name__}"
                )
                raise
            duration = time.perf_counter() - started
            log_llm_call(
                model=model,
                model_type=model_type,
                content=content,
                event=event,
                duration=duration,
                response=resp.content,
                usage=resp.usage,
            )
            log.debug(
                f"模型调用 {m_name} (类型: {model_type}) | 事件={event} | "
                f"耗时={duration:.3f}s | Tokens: {resp.usage} | 成功"
            )
            usage = resp.usage
            if isinstance(usage, TokenUsage):
                MTYM.add_usage(m_name, event, usage)
            if isinstance(resp, LLMResponse) and not resp.model:
                resp.model = m_name
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
        """调用指定类型的模型。选择顺序：

        1. **配置指定**（``set_preferred``）：固定用它，失败后按序转移；
        2. **确定性类型**（``DETERMINISTIC_TYPES``，embedding）：按注册顺序取第一个。
           这是「没配就等于固定」的兜底——向量必须始终落在同一语义空间；
        3. 其余类型：随机打散做负载均衡（这些调用彼此独立，无状态可比性要求）。

        :return: 模型的响应；``resp.model`` 回填的是**注册名**，
            需要真实模型 id 时用 ``llm_manager.get_model(resp.model).model``。
        """
        type_key: str = model_type.value
        models = self.get_models(type_key)
        if not models:
            log.warning(f"没有找到类型为 '{type_key}' 的模型")
            raise ModelRequestError(NO_MODEL_ERR)

        preferred = self._preferred.get(type_key)
        head = [m for m in models if m.name == preferred] if preferred else []
        rest = [m for m in models if m.name != preferred] if preferred else list(models)

        if head:
            candidates = head + rest
        elif type_key in DETERMINISTIC_TYPES or type_key in AGENT_TYPES:
            candidates = rest
            if not preferred and len(rest) > 1:
                if type_key not in self._multi_model_warned:
                    self._multi_model_warned.add(type_key)
                    log.warning(
                        f"类型 '{type_key}' 注册了多个模型但未指定，"
                        f"按注册顺序默认使用 {rest[0].name}（后续同类提示只记 debug）"
                    )
                else:
                    log.debug(f"类型 '{type_key}' 仍按注册顺序使用 {rest[0].name}")
        else:
            candidates = random.sample(rest, len(rest))

        for model in candidates:
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

    def embedding_identity(self) -> tuple[str, str]:
        """当前 Agent embedding 的 (注册名, 真实模型 id)；没有配置时返回空串对。

        记忆写入时要把这个标识存进 metadata：换模型后据此判断历史向量
        能不能拿来比（不同空间的余弦值不可比）。

        **解析顺序必须与 ``call_model_by_type`` 一致**，否则会出现
        「写入时用 A 的向量、记录成 B 的标识」这种更隐蔽的错误。
        """
        type_key = ModelType.ATRI_EMBEDDING.value
        names = self._typed_models.get(type_key) or []
        if not names:
            return "", ""
        preferred = self._preferred.get(type_key)
        name = preferred if preferred in names else names[0]
        primary = self._models[name]
        return primary.name, primary.model

    def clear_all(self):
        """清空所有模型与指定关系。

        必须连 ``_preferred`` 一起清：重载 provider 后模型名可能已经不存在，
        残留的指定会让下一次 ``set_preferred`` 之外的路径指向一个已消失的名字。
        """
        self._models.clear()
        self._typed_models.clear()
        self._preferred.clear()
        self._multi_model_warned.clear()

    def has_type(self, model_type: ModelType) -> bool:
        return model_type.value in self._typed_models

    def require(self, model_type: ModelType, label: str = "") -> LLMModel:
        """返回 Agent 类型的首选模型；缺失时抛 ``AgentModelMissingError``。

        选择规则与 ``call_model_by_type`` 一致：有 preferred 用 preferred，
        否则按注册顺序取第一个。调用方通常只需要「检查是否存在」，
        但返回模型可以让健康检查 / 日志直接拿到名字。
        """
        models = self.get_models(model_type.value)
        if not models:
            raise AgentModelMissingError(model_type, label)
        preferred = self._preferred.get(model_type.value)
        for model in models:
            if model.name == preferred:
                return model
        return models[0]

    async def call_agent(
        self,
        model_type: ModelType,
        content: str | LLMContents | list | Any,
        event: str = "Agent",
    ) -> LLMResponse:
        """Agent 专用调用入口：先 ``require``，再按类型确定性选择。"""
        self.require(model_type)
        return await self.call_model_by_type(model_type, content, event)
