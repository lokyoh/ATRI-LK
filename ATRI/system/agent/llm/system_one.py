from ..utils import log, request
from .contents import Sys1Content
from .model import LLMModel, LLMResponse, ModelRequestError
from .token import TokenUsage


class SystemOneModel(LLMModel):
    """
    Laya 模型类，继承自 LLMModel
    """

    def __init__(
        self,
        name: str,
        model: str,
        endpoint: str,
        api_key: str,
    ):
        super().__init__(name=name, model=model, endpoint=endpoint, api_key=api_key)

    async def call(self, content: Sys1Content) -> LLMResponse:
        if isinstance(content, Sys1Content):
            data = content.to_dict()
        else:
            raise ModelRequestError("Invalid content type for LayaModel")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }
        log.debug(f"模型输入: {content.get_shorten_content()}")
        resp = await request.post(url=self.endpoint, json=data, headers=headers)
        try:
            resp_json: dict = resp.json()
            usage: dict = resp_json.get("usage", {})
            usage_obj = TokenUsage(
                prompt_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                cache_hit_tokens=0,
            )
            answers = resp_json["answers"]
            log.debug(f"模型输出: {answers}\nTokens: {usage_obj}")
            return LLMResponse(content=answers, usage=usage)
        except Exception:
            try:
                resp_json: dict = resp.json()
                usage: dict = resp_json.get("usage", {})
                usage_obj = TokenUsage(
                    prompt_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    cache_hit_tokens=0,
                )
                answers = resp_json["answers"]
                log.debug(f"模型输出: {answers}\nTokens: {usage_obj!s}")
                return LLMResponse(content=answers, usage=usage)
            except Exception as e:
                log.debug(f"模型输出: {resp.text}")
                raise ModelRequestError(
                    f"请求失败了: {resp.text}", status_code=resp.status_code
                ) from e


def create_sys1_model(
    name,
    model,
    endpoint,
    api_key,
    temperature,
    data=None,
):
    return SystemOneModel(
        name=name,
        model=model,
        endpoint=endpoint,
        api_key=api_key,
    )
