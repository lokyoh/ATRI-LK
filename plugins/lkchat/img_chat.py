import os
from pathlib import Path
from google.genai import types

from ATRI.log import log
from ATRI.system.lkapi.ai.gemini import client, sub_model_name


def get_response(img_paths, text):
    prompt_parts = types.Content(role='user',parts=[])
    for path in img_paths:
        if not (img := Path(path)).exists():
            raise FileNotFoundError(f"Could not find image: {img}")
        prompt_parts.parts.append(
            types.Part.from_bytes(mime_type = "image/jpeg",data = Path(path).read_bytes())
        )
    prompt_parts.parts.append(
        types.Part.from_text(text=text)
    )
    response = client.models.generate_content(
        model=sub_model_name,
        contents=prompt_parts,
        config=types.GenerateContentConfig(
            response_mime_type="text/plain",
        )
    )
    log.info(response.text)
    for path in img_paths:
        try:
            os.remove(path)
        except OSError as e:
            log.warning(f"删除文件失败：{path}，原因：{e}")
    return response.text
