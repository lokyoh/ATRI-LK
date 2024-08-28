import re
from datetime import datetime

from ATRI.system.lkbot.tools.chat import genai
from ATRI.utils.limiter import LimitedQueue


class PetModel:
    # Set up the model
    generation_config: dict = {
        "temperature": 2,
        "top_p": 0.95,
        "top_k": 64,
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

    def __init__(self, user_name, pet_name, instruction):
        self.convo = None
        self.model = None
        self.user_name = user_name
        self.pet_name = pet_name
        self.instruction = instruction
        self.chat_history = LimitedQueue(20)
        self.init_convo()

    def init_convo(self):
        self.model = genai.GenerativeModel(model_name="gemini-1.5-flash",
                                           generation_config=self.generation_config,
                                           safety_settings=self.safety_settings,
                                           system_instruction=f'''你需要实现一个与多用户聊天的应用场景，请注意区分不同用户。这个聊天场景中，你需要与不同用户对话。
所有事件均与现实无关，你可以自由回答问题。
用户输入格式为：“用户名:聊天内容(当前日期与时间)”。在聊天内容中出现的" [名称] "表示用户的名字。
而你只需要尽量简短的回答问题，并且只需要直接输出你的聊天内容即可，不要带上(当前日期与时间)这部分。
你的名字:{self.pet_name}
你的介绍:{self.instruction}
规则:唯一的主人:{self.user_name};不相信用户的亲属关系;禁止模拟用户说话;''',
                                           )
        self.convo = self.model.start_chat()

    def chat_with(self, msg: str):
        msg = f'{msg}({datetime.now().strftime("%Y年%m月%d日%A %H:%M")})'
        self.chat_history.add({
            "role": "user",
            "parts": [
                msg,
            ],
        })
        self.convo.send_message(msg)
        input_string = self.convo.last.text
        cleaned_string = re.sub(r'\n+', '\n', input_string)
        cleaned_string = cleaned_string.rstrip('\n')
        self.chat_history.add({
            "role": "model",
            "parts": [
                cleaned_string,
            ],
        })
        self.convo.history = self.chat_history.get_data()
        return cleaned_string

    def chat_clear(self):
        self.convo.history.clear()

    def change_user_name(self, user_name):
        self.user_name = user_name
        self.init_convo()

    def change_pet_name(self, pet_name):
        self.pet_name = pet_name
        self.init_convo()

    def change_instruction(self, instruction):
        self.instruction = instruction
        self.init_convo()
