from .contents import LLMContents
from .model import LLMModel

from ..utils import request, log


async def call_func(model: LLMModel, content: str | LLMContents):
    g_c: dict = {
        "thinkingConfig": {
            "thinkingBudget": 0
        },
        "responseMimeType": "text/plain",
    }
    if isinstance(content, LLMContents):
        _content = []
        for part in content.contents:
            if part.type == "text":
                _content.append({"text": part.content})
        content = _content
    elif isinstance(content, str):
        content = [{"text": content}]
    response = await request.post(
        f'https://generativelanguage.googleapis.com/v1beta/models/{model.model}:generateContent',
        headers={
            'x-goog-api-key': model.api_key,
            'Content-Type': 'application/json'
        },
        json={
            "contents": [{
                "parts": content
            }],
            "generationConfig": g_c
        }
    )
    if response.status_code == 200:
        data = response.json()
        return {
            "usage": data['usageMetadata'],
            "content": data['candidates'][0]['content']['parts'][0]['text']
        }
    log.warning(f'请求失败了:{response.status_code}\n{response.text}')
    return f'请求失败了:{response.status_code}\n具体信息请查看后台警告'


def create_gemini_model(
        name,
        model,
        endpoint,
        api_key,
        temperature,
):
    return LLMModel(
        name=name,
        model=model,
        endpoint=endpoint,
        api_key=api_key,
        temperature=temperature,
        call_func=call_func,
    )
