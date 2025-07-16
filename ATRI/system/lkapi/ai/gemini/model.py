from google import genai
from google.genai import types
import re

from ATRI.utils.limiter import LimitedQueue

from ...bot.config import configs

try:
    client = genai.Client(api_key=configs.api_key)
except Exception:
    client = None

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


class Model:
    def __init__(self, model: str = sub_model_name, system_instruction: str = ''):
        self.model = model
        self.config = types.GenerateContentConfig(
            response_mime_type="text/plain",
        )
        if system_instruction:
            self.config.system_instruction = system_instruction

    async def generate_content(self, content: str):
        if client is None:
            raise RuntimeError('No Client.')
        response = await client.aio.models.generate_content(
            model=self.model,
            contents=content,
            config=self.config
        )
        return response.text

    def change_system_instruction(self, system_instruction: str):
        self.config.system_instruction = system_instruction


class Chats(Model):
    def __init__(self, model: str = sub_model_name, system_instruction: str = '', limit: int = 20):
        super().__init__(model, system_instruction)
        self.limit = limit
        self.history = LimitedQueue(self.limit)

    async def generate_content(self, content: str):
        if client is None:
            raise RuntimeError('No Client.')
        self.history.add(
            types.Content(
                role='user',
                parts=[
                    types.Part.from_text(text=content)
                ]
            )
        )
        response = await client.aio.models.generate_content(
            model=self.model,
            contents=self.history.get_data(),
            config=self.config
        )
        cleaned_string = re.sub(r'\n+', '\n', response.text)
        cleaned_string = cleaned_string.rstrip('\n')
        self.history.add(
            types.Content(
                role='model',
                parts=[
                    types.Part.from_text(text=cleaned_string)
                ]
            )
        )
        return cleaned_string

    def clear(self):
        self.history = LimitedQueue(self.limit)
