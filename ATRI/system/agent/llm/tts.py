from pathlib import Path

from ATRI.dir import TEMP_DIR
from ATRI.log import log
from ATRI.utils import gen_random_str

from ..utils import request
from .model import LLMModel, LLMResponse, ModelRequestError
from .registry import ProviderSpec, register_provider
from .speech import prepare_speech_text

AGENT_TEMP_DIR = TEMP_DIR / "agent"

DEFAULT_MAX_CHARS = 60
"""单条语音的字数上限默认值；超过就只发文字。"""


async def call_func(model: LLMModel, content: str) -> LLMResponse:
    """OpenAI 兼容的 ``/audio/speech``：返回合成好的音频路径。"""
    endpoint = model.endpoint.rstrip("/")
    if not endpoint.endswith("/audio/speech"):
        endpoint += "/audio/speech"
    headers = {"Content-Type": "application/json"}
    if model.api_key:
        headers["Authorization"] = f"Bearer {model.api_key}"

    response = await request.post(
        endpoint,
        json={
            "model": model.model,
            "input": content,
            "response_format": "mp3",
            "stream": False,
            **model.data,
        },
        headers=headers,
    )
    if response.status_code != 200:
        log.warning(
            f"请求失败，状态码: {response.status_code}\n错误信息: {response.text}"
        )
        raise ModelRequestError(
            f"请求失败了:{response.status_code}\n具体信息请查看后台警告",
            status_code=response.status_code,
        )

    AGENT_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    path = AGENT_TEMP_DIR / f"tts-{gen_random_str(4)}.mp3"
    path.write_bytes(response.content)
    log.info(f"音频已成功保存为 {path}")
    return LLMResponse(content=path)


def create_tts_model(
    name,
    model,
    endpoint,
    api_key,
    temperature,
    data=None,
    **kwargs,
):
    return LLMModel(
        name=name,
        model=model,
        endpoint=endpoint,
        api_key=api_key,
        temperature=temperature,
        data=data,
        call_func=call_func,
        **kwargs,
    )


register_provider(
    ProviderSpec(
        key="tts",
        label="语音合成",
        factory=create_tts_model,
        probe="ATRI 语音合成连通性测试",
        probe_cleanup=True,
    )
)


def _read_config_value(key: str):
    """读 Agent 配置；配置层还没就绪时返回 ``None``（走默认值）。"""
    try:
        from ..config import config

        return config.get(key)
    except Exception:
        return None


def _speech_limit(max_chars: int | None = None) -> int:
    """单条语音字数上限；0 或负数视为不限制。"""
    if max_chars is not None:
        limit = int(max_chars)
    else:
        value = _read_config_value("tts_max_chars")
        limit = DEFAULT_MAX_CHARS if value is None else int(value)
    return max(0, limit)


async def generate_audio(
    text: str,
    app_id: str = "tts",
    max_chars: int | None = None,
) -> Path | None:
    """按配置清洗文本并合成语音；没有模型或念不出内容时返回 ``None``。

    :param max_chars: 字数上限，超过直接放弃（长文本的语音又慢又吵）。
    """
    from . import llm_manager

    if not llm_manager.has_slot("tts"):
        log.debug("没有可用的语音模型，跳过语音回复")
        return None

    limit = _speech_limit(max_chars)
    body = prepare_speech_text(text)
    if not body:
        log.debug("文本没有可朗读的内容，跳过语音回复")
        return None
    if limit and len(body) > limit:
        # 超长回复配语音既慢又吵，只发文字；截断成半句话更难听。
        log.debug(f"文本 {len(body)} 字超过语音上限 {limit}，跳过语音回复")
        return None
    try:
        response = await llm_manager.call_agent(
            "tts", body, "文本转语音", app_id=app_id
        )
    except ModelRequestError as exc:
        log.warning(f"文本转语音失败：{exc}")
        return None
    path = response.content
    return path if isinstance(path, Path) else None
