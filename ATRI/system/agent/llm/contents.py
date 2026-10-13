class LLMContent:
    def __init__(self, _type, content):
        self.type = _type
        self.content = content

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
        return self

    def text(self, text: str):
        self.add_content(LLMContent("text", text))
        return self

    def image(self, image_base64: str):
        self.add_content(LLMContent("image", image_base64))
        return self

    def audio(self, audio_base64: str):
        self.add_content(LLMContent("audio", audio_base64))
        return self

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


class LLMMessage:
    def __init__(self, role: str, content: LLMContents):
        self.role = role
        self.content = content

    def get_shorten_content(self, text_len: int = 0, other_len: int = 20) -> str:
        return f"Role: {self.role}, Content: {self.content.get_shorten_content(text_len, other_len)}"


class LLMMessages:
    def __init__(self):
        self.messages: list[LLMMessage] = []

    def add_message(self, msg: LLMMessage):
        self.messages.append(msg)
        return self

    def add_response(self, msg: LLMMessage):
        self.messages.append(msg)
        return self

    def get_shorten_content(self, text_len: int = 0, other_len: int = 20) -> str:
        _shorten_content = ""
        for msg in self.messages:
            _shorten_content += msg.get_shorten_content(text_len, other_len) + "\n"
        return _shorten_content


class Sys1Question:
    def __init__(
        self,
        type: str,
        instructions: str,
        criteria: dict[str, str] | list[str] | None = None,
    ):
        self.type = type
        self.instructions = instructions
        self.criteria = criteria


class Sys1Content:
    def __init__(
        self,
        state: str | list[str] | dict[str, str],
        questions: dict[str, Sys1Question],
        model: str = "auto",
    ):
        self.state = state
        self.questions = questions
        self.model = model

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "questions": {
                key: {
                    "type": q.type,
                    "instructions": q.instructions,
                    **({"criteria": q.criteria} if q.criteria is not None else {}),
                }
                for key, q in self.questions.items()
            },
            "model": self.model,
        }

    def get_shorten_content(self) -> str:
        _shorten_content = f"State: {self.state}\n"
        for key, question in self.questions.items():
            _shorten_content += f"Question {key}: {question.type}, Instructions: {question.instructions}\n"
            if question.criteria:
                _shorten_content += f"Criteria: {question.criteria}\n"
        return _shorten_content
