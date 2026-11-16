import copy
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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
    """模型能力分类：通用 / 图片 / 语音 / 向量 / SystemOne。

    一个模型只按**能力**分类，不再按用途细分——同一个通用模型可以同时
    服务思考、聊天、判断、记忆等多个使用点，由配置层为每个使用点单独选。
    """

    GENERAL = "general"
    """通用文本模型：思考、聊天、判断、记忆、总结等"""
    IMAGE = "image"
    """图片理解"""
    TTS = "tts"
    """文本转语音"""
    EMBEDDING = "embedding"
    """向量"""
    SYSTEMONE = "sys1"
    """SystemOne 专用协议"""


MODEL_TYPE_LABELS: dict[str, str] = {
    "general": "通用",
    "image": "图片",
    "tts": "语音",
    "embedding": "向量",
    "sys1": "SystemOne",
}
"""类型的中文名与标签配色；WebUI 只显示，不自己维护一份。"""

MODEL_TYPE_COLORS: dict[str, str] = {
    "general": "success",
    "image": "info",
    "tts": "warning",
    "embedding": "primary",
    "sys1": "danger",
}


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
        cache_field: str = "",
        cache_field_locked: bool = False,
        stream: bool = False,
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
        self.cache_field: str = cache_field
        """缓存标识字段；空 = 不发"""
        self.cache_field_locked: bool = cache_field_locked
        """供应商定死了字段"""
        self.stream: bool = bool(stream)
        """流式输出"""

    async def call(
        self, content: str | LLMContents | list | Any, cache_key: str | None = None
    ) -> LLMResponse:
        """按序尝试多个 API 密钥，每次在模型副本上执行。

        :param cache_key: 配了 ``cache_field`` 才会随请求发出。
        """
        key_total = len(self.api_keys)
        attempts = self.api_keys or [None]
        errors: list[Exception] = []
        for index, api_key in enumerate(attempts):
            attempt = copy.copy(self)
            attempt.api_key = api_key
            try:
                return await attempt._call_once(content, cache_key=cache_key)
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

    def _build_payload(self, messages: list[dict], cache_key: str | None) -> dict:
        """请求体。默认是 OpenAI 形状；子类按自家协议覆盖。"""
        data = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            **self.data,
        }
        # 只认配置里选定的字段；data 里已显式填的优先。
        if self.cache_field and cache_key:
            data.setdefault(self.cache_field, cache_key)
        if self.stream:
            data["stream"] = True
        return data

    @staticmethod
    def _usage_from_dict(usage: dict[str, Any]) -> TokenUsage:
        """把 usage 字段翻译成 TokenUsage。默认 OpenAI 形状。"""
        return TokenUsage(
            prompt_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            cache_hit_tokens=usage.get("prompt_cache_hit_tokens", 0),
        )

    @staticmethod
    def _message_text(message: dict) -> str:
        """从一条 assistant 消息里取正文。默认只认 ``content``。"""
        return str(message.get("content") or "").lstrip("\n")

    async def _call_once(
        self,
        content: str | LLMContents | list | Any,
        cache_key: str | None = None,
    ) -> LLMResponse:
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
            data = self._build_payload(messages, cache_key)
            resp = await request.post(
                url=self.endpoint + "/chat/completions", json=data, headers=headers
            )
        else:
            raise ValueError(f"不支持的 HTTP 方法: {self.method}")
        if self.stream:
            return _parse_stream(resp, self._usage_from_dict)
        try:
            return self._parse_response(resp.json())
        except Exception:
            try:
                return self._parse_response(json.loads(resp.text))
            except Exception as e:
                raise ModelRequestError(
                    f"请求失败了: {resp.text}", status_code=resp.status_code
                ) from e

    def _parse_response(self, resp_json: dict) -> LLMResponse:
        """把一次完整响应翻成 LLMResponse。子类可覆盖。"""
        message = (resp_json.get("choices") or [{}])[0].get("message", {})
        usage_dict = resp_json.get("usage", {})
        return LLMResponse(
            content=self._message_text(message),
            usage=self._usage_from_dict(usage_dict),
        )


def _parse_stream(resp, usage_parser=None) -> LLMResponse:
    """把 SSE 分块流拼成完整响应。

    只换传输方式：上层拿到的仍是完整文本，长回复因此不会撞在读超时上。
    """
    if resp.status_code != 200:
        raise ModelRequestError(
            f"请求失败了: {resp.text}", status_code=resp.status_code
        )
    parts: list[str] = []
    usage_dict: dict[str, Any] = {}
    for line in resp.text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        chunk = line[5:].strip()
        if chunk == "[DONE]":
            break
        try:
            payload = json.loads(chunk)
        except json.JSONDecodeError:
            raise ModelRequestError(f"流式分块无法解析: {chunk[:200]}") from None
        if not isinstance(payload, dict):
            continue
        if payload.get("usage"):
            usage_dict = payload["usage"]
        for choice in payload.get("choices") or []:
            piece = (choice.get("delta") or {}).get("content")
            if piece:
                parts.append(piece)
    if not parts and not usage_dict:
        raise ModelRequestError(f"流式响应为空: {resp.text[:200]}")
    parser = usage_parser or LLMModel._usage_from_dict
    usage = parser(usage_dict)
    return LLMResponse(content="".join(parts).lstrip("\n"), usage=usage)


MODEL_TYPES: frozenset[str] = frozenset(mt.value for mt in ModelType)
"""全部模型类型。选型确定性：有指定用指定，否则按注册顺序。"""

DEVICE_ID_MAX = 8
"""设备标识长度上限（英文与数字，由配置层生成）。"""

APP_ID_MAX = 64
"""调用方标识长度上限，由调用处自己填。"""

CACHE_KEY_PREFIX = "atri"
"""缓存标识的固定开头。"""

_ID_RE = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True)
class SlotSpec:
    """一个模型使用点。"""

    key: str
    """标识，英文数字"""
    label: str
    config_field: str
    """AgentConfig 里选模型的字段名"""
    capabilities: tuple[str, ...]
    """可选能力类型，第一个是默认"""


SLOT_SPECS: tuple[SlotSpec, ...] = (
    SlotSpec("think", "思考", "model_think", ("general",)),
    SlotSpec("chat", "聊天", "model_chat", ("general",)),
    SlotSpec("judge", "判断", "model_judge", ("general",)),
    SlotSpec("memory", "记忆", "model_memory", ("general",)),
    SlotSpec("profile", "画像", "model_profile", ("general",)),
    SlotSpec("schedule", "日程", "model_schedule", ("general",)),
    SlotSpec("outfit", "着装", "model_outfit", ("general",)),
    SlotSpec("home", "家", "model_home", ("general",)),
    SlotSpec("summary", "总结", "model_summary", ("general",)),
    SlotSpec("vision", "图片理解", "model_image", ("image", "general")),
    SlotSpec("embedding", "向量", "model_embedding", ("embedding",)),
    SlotSpec("tts", "语音", "model_tts", ("tts",)),
)

SLOTS: dict[str, SlotSpec] = {spec.key: spec for spec in SLOT_SPECS}


def cache_identity(app_id: str = "", device_id: str = "") -> str | None:
    """缓存标识：atri-{设备}-{调用方}。

    app_id 由调用处填，没有就不发；含非法字符或超长时整段丢弃。
    """
    app = _ID_RE.fullmatch(str(app_id or "").strip())
    device = _ID_RE.fullmatch(str(device_id or "").strip())
    if not app or not device:
        return None
    if len(app.group()) > APP_ID_MAX or len(device.group()) > DEVICE_ID_MAX:
        return None
    return f"{CACHE_KEY_PREFIX}-{device.group()}-{app.group()}"


class AgentModelMissingError(ModelRequestError):
    """配置问题，不是请求失败：入口应转成用户可见提示。"""

    def __init__(self, model_type: ModelType, label: str = ""):
        self.model_type = model_type
        self.label = label or model_type.value
        super().__init__(f"未配置{self.label}可用的模型")


class LLMManager:
    """模型注册与调用。"""

    def __init__(self):
        self._models: dict[str, LLMModel] = {}
        self._typed_models: dict[str, list[str]] = {}
        self._preferred: dict[str, str] = {}
        #: 已提示过「多模型未指定」的使用点，避免每轮刷告警。
        self._multi_model_warned: set[str] = set()
        self.device_id: str = ""  # 缓存标识的设备段，由配置层注入

    def set_preferred(self, slot: str, model_name: str) -> bool:
        """指定使用点用哪个模型；空串 = 清除。

        失败（模型不存在或能力不符）时保留原值，调用方负责告警。
        """
        spec = SLOTS.get(str(slot))
        if spec is None:
            return False
        name = (model_name or "").strip()
        if not name:
            self._preferred.pop(spec.key, None)
            return True
        if name not in self._models:
            return False
        if not self.model_fits_slot(name, spec.key):
            return False
        self._preferred[spec.key] = name
        return True

    def get_preferred(self, slot: str) -> str:
        return self._preferred.get(str(slot), "")

    def model_fits_slot(self, model_name: str, slot: str) -> bool:
        """这个模型能否被该使用点使用——只看能力类型，不看用途。"""
        spec = SLOTS.get(str(slot))
        if spec is None:
            return False
        return any(
            model_name in (self._typed_models.get(cap) or [])
            for cap in spec.capabilities
        )

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
        """该类型下已注册的模型；没注册时返回空列表。"""
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
        cache_key: str | None = None,
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
                resp: LLMResponse = await model.call(content, cache_key=cache_key)
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
        slot: str = "",
        app_id: str = "",
    ) -> LLMResponse:
        """调用指定能力类型的模型，按使用点选型。

        选择顺序：先看 ``slot`` 有没有指定（同一使用点固定一个模型，
        缓存前缀才稳定），再退到该能力类型注册的第一个。

        :param slot: 使用点标识，用于选型（哪个模型），不参与缓存标识。
        :param app_id: 调用方标识，缓存的唯一外部输入；空 = 不发缓存标识。
        :return: 模型的响应；``resp.model`` 回填的是**注册名**，
            需要真实模型 id 时用 ``llm_manager.get_model(resp.model).model``。
        """
        type_key: str = model_type.value
        models = self.get_models(type_key)
        if not models:
            log.warning(f"没有找到类型为 '{type_key}' 的模型")
            raise ModelRequestError(NO_MODEL_ERR)

        preferred = self._preferred.get(slot, "") if slot else ""
        candidates = [m for m in models if m.name == preferred] or list(models)
        if not preferred and len(models) > 1 and slot:
            if slot not in self._multi_model_warned:
                self._multi_model_warned.add(slot)
                log.warning(
                    f"使用点 '{slot}' 注册了多个 {type_key} 模型但未指定，"
                    f"按注册顺序使用 {models[0].name}（后续同类提示只记 debug）"
                )
            else:
                log.debug(f"使用点 '{slot}' 仍按注册顺序使用 {models[0].name}")

        cache_key = cache_identity(app_id, self.device_id)
        for model in candidates:
            times = 0
            while True:
                try:
                    return await self.call_model(model, content, event, cache_key)
                except Exception:
                    pass
                times += 1
                if times >= 3:
                    break
        raise ModelRequestError(ALL_MODEL_RESP_ERR)

    def embedding_identity(self) -> tuple[str, str]:
        """当前向量模型的 (注册名, 真实模型 id)；没有配置时返回空串对。

        记忆写入时要把这个标识存进 metadata：换模型后据此判断历史向量
        能不能拿来比（不同空间的余弦值不可比）。

        **解析顺序必须与 ``call_model_by_type`` 一致**：先看使用点指定，
        再按注册顺序取第一个。
        """
        type_key = ModelType.EMBEDDING.value
        names = self._typed_models.get(type_key) or []
        if not names:
            return "", ""
        preferred = self._preferred.get("embedding", "")
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

    def has_slot(self, slot: str) -> bool:
        """某个使用点此刻能不能调用——取决于它有没有可用的模型。"""
        return self.model_for_slot(slot) is not None

    def capability_for_slot(self, slot: str) -> str:
        """该使用点此刻会用哪个能力类型；没有可用模型时返回空串。

        顺序：使用点指定（若它确实具备该能力）→ 该使用点可选列表里的第一个
        有注册模型的能力类型。
        """
        spec = SLOTS.get(str(slot))
        if spec is None:
            return ""
        preferred = self._preferred.get(spec.key, "")
        if preferred and preferred in self._models:
            for capability in spec.capabilities:
                if preferred in (self._typed_models.get(capability) or []):
                    return capability
        for capability in spec.capabilities:
            if self.get_models(capability):
                return capability
        return ""

    def model_for_slot(self, slot: str) -> LLMModel | None:
        """某使用点最终会用到的模型；该使用点没有任何可用模型时返回 None。"""
        capability = self.capability_for_slot(slot)
        if not capability:
            return None
        preferred = self._preferred.get(str(slot), "")
        if preferred and self._models.get(preferred):
            return self._models[preferred]
        models = self.get_models(capability)
        return models[0] if models else None

    async def call_agent(
        self,
        slot: str,
        content: str | LLMContents | list | Any,
        event: str = "Agent",
        app_id: str = "",
    ) -> LLMResponse:
        """Agent 专用入口：按使用点选模型，缺失时抛 ``AgentModelMissingError``。"""
        spec = SLOTS.get(str(slot))
        if spec is None:
            raise ModelRequestError(f"未知模型使用点: {slot}")
        capability = self.capability_for_slot(spec.key)
        if not capability:
            raise AgentModelMissingError(ModelType.GENERAL, spec.label)
        return await self.call_model_by_type(
            ModelType(capability), content, event, slot=spec.key, app_id=app_id
        )
