from ..utils import request
from .contents import Sys1Content, Sys1Question
from .model import LLMModel, LLMResponse, ModelRequestError
from .registry import ProviderSpec, register_provider

PROBE = Sys1Content(
    state="ATRI 模型 API 连通性测试",
    questions={
        "connectivity": Sys1Question(
            type="noul",
            instructions="确认请求成功，并简短回复 OK。",
        )
    },
)


class SystemOneModel(LLMModel):
    """SystemOne 专用协议：请求体与响应结构都与 OpenAI 不同。"""

    async def _call_once(
        self, content: Sys1Content, cache_key: str | None = None
    ) -> LLMResponse:
        if not isinstance(content, Sys1Content):
            raise ModelRequestError("SystemOne 只接受 Sys1Content")
        headers = {"Authorization": f"Bearer {self.api_key}"}
        resp = await request.post(
            url=self.endpoint, json=content.to_dict(), headers=headers
        )
        try:
            body: dict = resp.json()
            return LLMResponse(content=body["answers"], usage=body.get("usage", {}))
        except Exception as e:
            raise ModelRequestError(
                f"请求失败了: {resp.text}", status_code=resp.status_code
            ) from e


def create_sys1_model(name, model, endpoint, api_key, temperature, data=None, **kwargs):
    return SystemOneModel(name=name, model=model, endpoint=endpoint, api_key=api_key)


register_provider(
    ProviderSpec(
        key="sys1",
        label="SystemOne",
        factory=create_sys1_model,
        probe=PROBE,
    )
)
