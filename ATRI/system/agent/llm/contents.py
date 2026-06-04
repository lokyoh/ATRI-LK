class LLMContent:
    def __init__(self, _type, content):
        self.type = _type
        self.content = content

    def text(self, text: str):
        self.type = 'text'
        self.content = text
        return self

    def image(self, image_base64: str):
        self.type = 'image'
        self.content = image_base64
        return self

    def audio(self, audio_base64: str):
        self.type = 'audio'
        self.content = audio_base64
        return self


class LLMContents:
    def __init__(self):
        self.contents: list[LLMContent] = []

    def add_content(self, content: LLMContent):
        self.contents.append(content)
