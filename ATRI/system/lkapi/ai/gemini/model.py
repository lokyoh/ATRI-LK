import json

from ATRI.utils import request
from ATRI.log import log

from .. import BaseChat
from ...bot.config import configs

model_name = 'gemini-2.5-flash'
"""主模型名称"""
sub_model_name = 'gemini-2.0-flash'
"""副模型名称"""


def set_model_name(name: str):
    """设置主模型名称"""
    global model_name
    model_name = name


def set_sub_model_name(name: str):
    """设置副模型名称"""
    global sub_model_name
    sub_model_name = name


class GeminiModel(BaseChat):
    def __init__(self, model: str = sub_model_name):
        self.model = model

    async def generate_content(self, content: str, r_type: str = 'text', data: dict = None):
        g_c: dict = {"thinkingConfig": {
            "thinkingBudget": 0
        }}
        if r_type == 'json':
            g_c["responseMimeType"] = "application/json"
            g_c["responseSchema"] = data
        else:
            g_c["responseMimeType"] = "text/plain"
        response = await request.post(
            f'https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent',
            headers={
                'x-goog-api-key': configs.api_key,
                'Content-Type': 'application/json'
            },
            json={
                "contents": [{
                    "parts": [
                        {"text": content}
                    ]
                }],
                "generationConfig": g_c
            }
        )
        if response.status_code == 200:
            data = response.json()
            text = data['candidates'][0]['content']['parts'][0]['text']
            log.debug(f'{self.model}生成成功:Token共使用{data['usageMetadata']['totalTokenCount']}')
            if r_type == 'json':
                return json.loads(text)
            return text
        log.warning(f'请求失败了:{response.status_code}\n{response.text}')
        return f'请求失败了:{response.status_code}\n具体信息请查看后台警告'
