import google.generativeai as genai

from ..config import config

genai.configure(api_key=config.api_key)

model_name = 'gemini-1.5-pro'
sub_model_name = 'gemini-1.5-flash'


def set_model_name(name: str):
    global model_name
    model_name = name


def set_sub_model_name(name: str):
    global sub_model_name
    sub_model_name = name


block_none_safety_settings = [
    {
        "category": "HARM_CATEGORY_HARASSMENT",
        "threshold": "BLOCK_NONE"
    },
    {
        "category": "HARM_CATEGORY_HATE_SPEECH",
        "threshold": "BLOCK_NONE"
    },
    {
        "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "threshold": "BLOCK_NONE"
    },
    {
        "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
        "threshold": "BLOCK_NONE"
    },
]

default_generation_config: dict = {
    "temperature": 1,
    "top_p": 1,
    "top_k": 64,
    "max_output_tokens": 2048,
    "response_mime_type": "text/plain",
}
