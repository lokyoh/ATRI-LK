from .model import LLMModel


def create_openai_model(
        name,
        model,
        endpoint,
        api_key,
        temperature,
):
    return LLMModel(
        name=name,
        model=model,
        endpoint=endpoint,
        api_key=api_key,
        temperature=temperature,
    )
