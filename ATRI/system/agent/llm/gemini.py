from ..utils import log, request
from .contents import LLMContents, LLMMessages
from .model import LLMModel, LLMResponse, ModelRequestError
from .token import TokenUsage


async def call_func(
    model: LLMModel, content: str | LLMContents | LLMMessages
) -> LLMResponse:
    if isinstance(content, LLMContents):
        log_content = ""
        _content = []
        for part in content.contents:
            if part.type == "text":
                _content.append({"text": part.content})
                log_content += part.content
        contents = [{"parts": _content}]
    elif isinstance(content, str):
        log_content = content
        contents = [{"parts": [{"text": content}]}]
    elif isinstance(content, LLMMessages):
        log_content = content.get_shorten_content()
        contents = []
        for msg in content.messages:
            _parts = []
            for part in msg.content.contents:
                if part.type == "text":
                    _parts.append({"text": part.content})
            contents.append({"role": msg.role, "parts": _parts})
    else:
        raise TypeError
    log.debug(f"模型输入: {log_content}")
    g_c: dict = {
        "responseMimeType": "text/plain",
    }
    if model.model.startswith("gemini"):
        g_c["thinkingConfig"] = {"thinkingBudget": 0}
    elif model.model.startswith("gemma"):
        g_c["thinkingConfig"] = {"thinkingLevel": "MINIMAL"}
    response = await request.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model.model}:generateContent",
        headers={"x-goog-api-key": model.api_key, "Content-Type": "application/json"},
        json={"contents": contents, "generationConfig": g_c},
    )
    if response.status_code == 200:
        data = response.json()
        usage_dict = data["usageMetadata"]
        usage = TokenUsage(
            prompt_tokens=usage_dict["promptTokenCount"],
            output_tokens=usage_dict["candidatesTokenCount"],
            cache_hit_tokens=usage_dict.get("cachedContentTokenCount", 0),
        )
        return LLMResponse(
            content=data["candidates"][0]["content"]["parts"][0]["text"],
            usage=usage,
        )
    log.warning(f"请求失败了:{response.status_code}\n{response.text}")
    raise ModelRequestError(
        f"请求失败了:{response.status_code}\n具体信息请查看后台警告",
        status_code=response.status_code,
    )


def create_gemini_model(
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
