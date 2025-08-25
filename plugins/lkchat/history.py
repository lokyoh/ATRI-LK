from datetime import datetime

from ATRI.utils.limiter import LimitedQueue

from . import config


class History:
    def __init__(self, sender, text):
        self.sender = sender
        self.time = datetime.now().strftime("%m月%d日%A %H:%M")
        self.text = text


class Dialogue:
    def __init__(self, history: History, text):
        self.sender = history.sender
        self.time = history.time
        self.text = history.text
        self.resp = text


class ChatHistory:
    def __init__(self):
        self.history = LimitedQueue(config.max_history)
        self.dialogues = LimitedQueue(config.max_dialogue)

    def add_history(self, user_id, text):
        self.history.add(History(user_id, text))

    def add_dialogue(self, text):
        self.dialogues.add(Dialogue(self.history.pop(), text))

    def get_history(self):
        return self.history.get_data()[:-1]

    def get_dialogues(self):
        return self.dialogues.get_data()

    def get_last_history(self):
        return self.history.get_data()[-1]
