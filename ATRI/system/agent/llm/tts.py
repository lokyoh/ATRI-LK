from ATRI.dir import TEMP_DIR
from ATRI.log import log
from ATRI.utils import gen_random_str

from ..config import config
from ..utils import request

AGENT_TEMP_DIR = TEMP_DIR / "agent"
AGENT_TEMP_DIR.mkdir(parents=True, exist_ok=True)

import re

async def generate_audio(text: str):
    text = re.sub(r'[(（][^)）]*[)）]', '', text)
    payload = {
        "model": config.tts.model,
        "input": text,
        "voice": config.tts.voice_id,
        "response_format": "mp3",
        "stream": False
    }

    headers = {
        "Authorization": f"Bearer {config.tts.api_key}",
        "Content-Type": "application/json"
    }

    response = await request.post(config.tts.url, json=payload, headers=headers)

    if response.status_code == 200:
        # 将流式响应写入文件
        path = AGENT_TEMP_DIR / f"tts-{gen_random_str(4)}.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            f.write(response.content)
        log.info(f"音频已成功保存为 {path}")
        return path
    else:
        log.warning(f"请求失败，状态码: {response.status_code}\n错误信息: {response.text}")
        return None