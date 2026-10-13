import re
from pathlib import Path

from ATRI.dir import TEMP_DIR
from ATRI.log import log
from ATRI.utils import gen_random_str

from ..utils import request
from .model import LLMModel, LLMResponse, ModelRequestError, ModelType

AGENT_TEMP_DIR = TEMP_DIR / "agent"


async def call_func(model: LLMModel, content: str) -> LLMResponse:
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
):
    return LLMModel(
        name=name,
        model=model,
        endpoint=endpoint,
        api_key=api_key,
        temperature=temperature,
        data=data,
        call_func=call_func,
    )


async def generate_audio(text: str) -> Path | None:
    from . import llm_manager

    if not llm_manager.has_type(ModelType.TTS):
        log.warning("没有配置 tts 类型的模型")
        return None

    text = re.sub(r"[(（][^)）]*[)）]", "", text)
    try:
        response = await llm_manager.call_model_by_type(
            ModelType.TTS, text, "文本转语音"
        )
    except ModelRequestError as exc:
        log.warning(f"文本转语音失败：{exc}")
        return None
    return response.content
