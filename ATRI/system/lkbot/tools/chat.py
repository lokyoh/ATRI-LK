import google.generativeai as genai

from ..config import config

genai.configure(api_key=config.config.api_key)