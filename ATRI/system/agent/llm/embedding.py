from ..utils import log, request
from .model import LLMModel, LLMResponse, ModelRequestError


async def call_func(model: LLMModel, content: str | list) -> LLMResponse:
    endpoint = model.endpoint.rstrip("/")
    headers = {"Content-Type": "application/json"}
    if model.api_key:
        headers["Authorization"] = f"Bearer {model.api_key}"
    response = await request.post(
        endpoint,
        json={"model": model.model, "input": content},
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
    return LLMResponse(content=[item["embedding"] for item in response.json()["data"]])


def create_embedding_model(
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
