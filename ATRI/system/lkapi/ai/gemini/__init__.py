from .model import (
    model_name,
    sub_model_name,
    set_model_name,
    set_sub_model_name,
    GeminiModel
)

from .. import chat_manager

chat_manager.register('gemini', GeminiModel())
chat_manager.register('gemini-main', GeminiModel(model_name))
