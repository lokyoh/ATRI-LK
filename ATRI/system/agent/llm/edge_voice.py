"""内置 Edge 语音合成供应商（edge-tts）。

为什么内置：``lkbot/tools/rec_editor.py`` 里原本就有一份 edge-tts 的调用，
但它只能同步、写死音色，而且和 Agent 的模型体系完全无关。这里把它接成一个
正常的 tts 供应商：仍走 ``llm_manager.call_agent("tts", ...)`` 这条路径，
既可以用内置 Edge，也可以换成任意 OpenAI 兼容的 ``/audio/speech`` 服务。

不需要 API 密钥，但需要能访问微软的语音服务（大陆网络可能需要代理，可在模型
的「附加配置」里填 ``proxy``）。只注册 ``tts`` 能力，不挤占任何文本使用点。
"""

from __future__ import annotations

import re

from ATRI.utils import gen_random_str

from .model import LLMModel, LLMResponse, ModelRequestError
from .registry import ProviderSpec, register_provider
from .tts import AGENT_TEMP_DIR

PROVIDER_KEY = "edge-tts"
"""供应商类型标识"""

PROVIDER_NAME = "edge-tts"
"""内置供应商的显示名；用户自己建了同名供应商时以用户的为准"""

DEFAULT_VOICE = "zh-CN-XiaoyiNeural"
"""默认音色：与 lkbot 里那份保持一致"""

DEFAULT_MODEL_NAME = "edge-tts/晓伊"
"""内置模型的注册名；语音使用点的下拉里看到的就是它"""

#: 合成参数默认值；用户可以在模型的「附加配置」里逐项覆盖。
DEFAULT_DATA: dict[str, object] = {
    "voice": DEFAULT_VOICE,
    "rate": "-10%",
    "pitch": "+30Hz",
    "volume": "+0%",
}

#: 单次合成失败后由 ``LLMManager`` 统一重试；这里不再叠加重试，
#: 否则两层各重试几次，一次失败会占用十几秒。
MAX_ATTEMPTS = 1

#: 已经告警过的参数值，避免每次合成都刷同一条 warning。
_WARNED: set[tuple[str, str]] = set()

#: 音色的两种合法写法：短名 ``zh-CN-XiaoyiNeural``，或 Edge 内部使用的长名。
_VOICE_SHORT_RE = re.compile(r"^[a-z]{2,}-[A-Z]{2,}-.+Neural$")
_VOICE_LONG_RE = re.compile(r"^Microsoft Server Speech Text to Speech Voice \(.+,.+\)$")
_PERCENT_RE = re.compile(r"^([+-]?)\s*(\d+(?:\.\d+)?)\s*[%％]?$")
_HZ_RE = re.compile(r"^([+-]?)\s*(\d+(?:\.\d+)?)\s*(?:Hz|hz|HZ)?$")


def _warn_once(field: str, value: str, message: str) -> None:
    from ATRI.log import log

    if (field, value) in _WARNED:
        return
    _WARNED.add((field, value))
    log.warning(message)


def normalize_voice(value: str) -> str:
    """把音色收成 edge-tts 7.x 认的两种写法之一。

    7.x 起 ``TTSConfig`` 会**严格校验**音色，写错一个字符直接 ValueError；
    用户容易漏掉结尾的 ``Neural``（``zh-CN-Xiaoyi``），这里补上再校验。
    """
    voice = str(value or "").strip()
    if _VOICE_LONG_RE.match(voice):
        return voice
    if _VOICE_SHORT_RE.match(voice):
        return voice
    if re.match(r"^[a-z]{2,}-[A-Z]{2,}-[A-Za-z0-9_]+$", voice):
        patched = f"{voice}Neural"
        if _VOICE_SHORT_RE.match(patched):
            _warn_once(
                "voice",
                voice,
                f"音色 {voice} 缺少 Neural 后缀，已按 {patched} 合成",
            )
            return patched
    _warn_once(
        "voice",
        voice,
        f"音色 {voice} 不是 Edge 认识的写法，已改用默认音色 {DEFAULT_VOICE}；"
        "正确写法如 zh-CN-XiaoyiNeural",
    )
    return DEFAULT_VOICE


def normalize_percent(value: str, default: str) -> str:
    """语速 / 音量的百分比：``-10%``、``10%``、``-10`` 都收成 ``±N%``。

    7.x 只接受 ``^[+-]\\d+%$``，少了符号或百分号会直接报错；没写符号按 ``+``。
    """
    body = str(value or "").strip()
    match = _PERCENT_RE.match(body)
    if match:
        sign = match.group(1) or "+"
        # 7.x 的正则只认整数，小数先取整再格式化。
        number = int(float(match.group(2)))
        return f"{sign}{number}%"
    _warn_once(
        "percent",
        body,
        f"语音参数 {body} 不是「±N%」写法，已改用默认值 {default}",
    )
    return default


def normalize_hz(value: str, default: str) -> str:
    """音调的频率值：``+30Hz``、``30`` 都收成 ``±NHz``。"""
    body = str(value or "").strip()
    match = _HZ_RE.match(body)
    if match:
        sign = match.group(1) or "+"
        return f"{sign}{int(float(match.group(2)))}Hz"
    _warn_once(
        "hz",
        body,
        f"语音参数 {body} 不是「±NHz」写法，已改用默认值 {default}",
    )
    return default


def _timeout(data: dict, key: str, default: int) -> int:
    try:
        value = int(data.get(key) or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _coerce_params(data: dict) -> dict[str, str]:
    """把附加配置里的合成参数收成 7.x 认的写法。"""
    return {
        "voice": normalize_voice(data.get("voice") or DEFAULT_VOICE),
        "rate": normalize_percent(
            data.get("rate") or DEFAULT_DATA["rate"], DEFAULT_DATA["rate"]
        ),
        "volume": normalize_percent(
            data.get("volume") or DEFAULT_DATA["volume"], DEFAULT_DATA["volume"]
        ),
        "pitch": normalize_hz(
            data.get("pitch") or DEFAULT_DATA["pitch"], DEFAULT_DATA["pitch"]
        ),
    }


def _build_communicate(edge_tts, text: str, params: dict[str, str], proxy, data: dict):
    return edge_tts.Communicate(
        text=text,
        voice=params["voice"],
        rate=params["rate"],
        volume=params["volume"],
        pitch=params["pitch"],
        proxy=proxy,
        connect_timeout=_timeout(data, "connect_timeout", 10),
        receive_timeout=_timeout(data, "receive_timeout", 60),
    )


async def call_func(model: LLMModel, content: str) -> LLMResponse:
    """把文本合成到临时目录的 mp3，返回路径（与 HTTP tts 供应商同形）。"""
    text = str(content or "").strip()
    if not text:
        raise ModelRequestError("待合成的文本为空")

    try:
        import edge_tts
    except ImportError as exc:  # pragma: no cover - 依赖缺失时给出可操作提示
        raise ModelRequestError("缺少 edge-tts 依赖，请先执行 poetry install") from exc

    data = dict(DEFAULT_DATA)
    data.update({key: value for key, value in (model.data or {}).items() if value})
    proxy = str(data.get("proxy") or "").strip() or None

    # 7.x 起 TTSConfig 会严格校验音色与语速/音量/音调，写错直接 ValueError；
    # 用户手填的值先收成合法写法，再交给库。
    params = _coerce_params(data)

    AGENT_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    path = AGENT_TEMP_DIR / f"tts-{gen_random_str(6)}.mp3"
    last_error: Exception | None = None
    for _ in range(MAX_ATTEMPTS):
        try:
            # 必须用异步 save：同步版会堵住整个事件循环，这期间其它消息发不出去。
            tts = _build_communicate(edge_tts, text, params, proxy, data)
            await tts.save(str(path))
            if path.exists() and path.stat().st_size > 0:
                return LLMResponse(content=path)
            # 空文件发出去会变成一条废消息，按失败处理并重试。
            path.unlink(missing_ok=True)
            last_error = ModelRequestError("Edge 语音合成没有产出音频")
        except Exception as exc:
            path.unlink(missing_ok=True)
            last_error = exc
    raise ModelRequestError(f"Edge 语音合成失败：{last_error}")


def create_edge_tts_model(
    name,
    model,
    endpoint,
    api_key,
    temperature,
    data=None,
    **kwargs,
):
    """edge-tts 不走 HTTP；``endpoint`` / ``api_key`` 只为兼容统一工厂签名。"""
    merged = dict(DEFAULT_DATA)
    merged.update(data or {})
    return LLMModel(
        name=name,
        model=model or DEFAULT_VOICE,
        endpoint=endpoint or "",
        api_key=api_key,
        temperature=temperature,
        data=merged,
        call_func=call_func,
        **kwargs,
    )


def _has_other_tts_model() -> bool:
    """除内置模型外，用户是否已经配了别的语音模型。"""
    from .provider import ProviderManager

    for provider in ProviderManager.providers.values():
        if provider.provider_type == PROVIDER_KEY:
            continue
        for model in provider.models:
            types = [model.type] if isinstance(model.type, str) else model.type or []
            if "tts" in types:
                return True
    return False


def ensure_default_provider() -> bool:
    """没有可用的语音模型时补一个内置的 edge-tts。

    只在内存里补，本函数**不写** ``provider.yml``：自动改盘会覆盖用户正在编辑
    的内容，也容易和 WebUI 的保存流程打架。补进来的供应商等同普通供应商：
    用户可以在 WebUI 里改音色，或者自建一个同名供应商把内置的顶掉；一旦在
    WebUI 里保存过 provider，它也会随其它供应商一起落盘，成为可持久编辑的一项。

    已经有用户自己的语音模型时直接跳过：否则「语音」使用点会同时看到两个模型，
    每次都按注册顺序挑一个，等于用默认值盖掉用户的选择。

    :return: 本次是否补了默认供应商。
    """
    from .provider import LLMModel as ProviderModel
    from .provider import LLMProvider, ProviderManager

    if PROVIDER_NAME in ProviderManager.providers:
        return False
    if any(
        provider.provider_type == PROVIDER_KEY
        for provider in ProviderManager.providers.values()
    ):
        return False
    if _has_other_tts_model():
        return False

    ProviderManager.providers[PROVIDER_NAME] = LLMProvider(
        provider_name=PROVIDER_NAME,
        provider_type=PROVIDER_KEY,
        url="",
        models=[
            ProviderModel(
                name=DEFAULT_MODEL_NAME,
                model=DEFAULT_VOICE,
                temperature=0.7,
                type=["tts"],
                data=dict(DEFAULT_DATA),
            )
        ],
    )
    return True


register_provider(
    ProviderSpec(
        key=PROVIDER_KEY,
        label="Edge TTS（内置）",
        factory=create_edge_tts_model,
        probe="亚托莉语音合成连通性测试",
        probe_cleanup=True,
    )
)


__all__ = [
    "DEFAULT_DATA",
    "DEFAULT_MODEL_NAME",
    "DEFAULT_VOICE",
    "MAX_ATTEMPTS",
    "PROVIDER_KEY",
    "PROVIDER_NAME",
    "call_func",
    "create_edge_tts_model",
    "ensure_default_provider",
    "normalize_hz",
    "normalize_percent",
    "normalize_voice",
]
