import os
from pathlib import Path

from ATRI.log import log
from ATRI.system.lkbot.tools.chat import genai

generation_config = {
    "temperature": 0.4,
    "top_p": 1,
    "top_k": 32,
    "max_output_tokens": 1024,
}

safety_settings = [
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

model = genai.GenerativeModel(model_name="gemini-1.5-flash",
                              generation_config=generation_config,
                              safety_settings=safety_settings)


def get_response(img_paths, text):
    prompt_parts = []
    for path in img_paths:
        if not (img := Path(path)).exists():
            raise FileNotFoundError(f"Could not find image: {img}")
        prompt_parts.append({
            "mime_type": "image/jpeg",
            "data": Path(path).read_bytes()
        })
    prompt_parts.append(text)
    response = model.generate_content(prompt_parts)
    log.info(response.text)
    for path in img_paths:
        try:
            os.remove(path)
        except OSError as e:
            log.warning(f"删除文件失败：{path}，原因：{e}")
    return response.text
