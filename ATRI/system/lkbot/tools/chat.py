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
