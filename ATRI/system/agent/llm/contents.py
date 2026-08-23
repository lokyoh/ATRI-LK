class LLMContent:
    def __init__(self, _type, content):
        self.type = _type
        self.content = content

    def text(self, text: str):
        self.type = "text"
        self.content = text
        return self

    def image(self, image_base64: str):
        self.type = "image"
        self.content = image_base64
        return self

    def audio(self, audio_base64: str):
        self.type = "audio"
        self.content = audio_base64
        return self

    def get_content(self, max_len: int = 0) -> str:
        if self.type == "text":
            if max_len and max_len > 0:
                return self.content[:max_len]
            else:
                return self.content
        else:
            if max_len and max_len > 0:
                return f"[{self.type}:'{self.content[:max_len]}']"
            else:
                return f"[{self.type}:'{self.content}']"


class LLMContents:
    def __init__(self):
        self.contents: list[LLMContent] = []

    def add_content(self, content: LLMContent):
        self.contents.append(content)

    def get_contents(self) -> list:
        _content = []
        for part in self.contents:
            if part.type == "text":
                _content.append({"type": "text", "text": part.content})
            elif part.type == "image":
                _content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": part.content, "detail": "high"},
                    }
                )
            elif part.type == "audio":
                _content.append(
                    {"type": "audio_url", "audio_url": {"url": part.content}}
                )
        return _content

    def get_shorten_content(self, text_len: int = 0, other_len: int = 20) -> str:
        _shorten_content = ""
        for part in self.contents:
            if part.type == "text":
                _shorten_content += part.get_content(text_len)
            else:
                _shorten_content += part.get_content(other_len)
        return _shorten_content
