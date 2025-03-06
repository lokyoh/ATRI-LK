import google.generativeai as genai

from ..config import config

genai.configure(api_key=config.api_key)

model_name = 'gemini-1.5-pro'
sub_model_name = 'gemini-1.5-flash'
