from ..config import config
from ..utils import log, request


async def embedding(text: str | list) -> list:
    _config = config.embedding
    payload = {"model": _config.model, "input": text}

    headers = {
        "Authorization": f"Bearer {_config.api_key}",
        "Content-Type": "application/json",
    }

    response = await request.post(_config.url, json=payload, headers=headers)

    if response.status_code == 200:
        resp = response.json()
        return [e["embedding"] for e in resp["data"]]
    else:
        log.warning(
            f"请求失败，状态码: {response.status_code}\n错误信息: {response.text}"
        )
        raise RuntimeError()
