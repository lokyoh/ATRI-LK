from ..utils import request
from .contents import Sys1Content
from .model import LLMModel, LLMResponse, ModelRequestError


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
        resp = await request.post(url=self.endpoint, json=data, headers=headers)
        try:
            resp_json: dict = resp.json()
            usage: dict = resp_json.get("usage", {})
            answers = resp_json["answers"]
            return LLMResponse(content=answers, usage=usage)
        except Exception:
            try:
                resp_json: dict = resp.json()
                usage: dict = resp_json.get("usage", {})
                answers = resp_json["answers"]
                return LLMResponse(content=answers, usage=usage)
            except Exception as e:
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
