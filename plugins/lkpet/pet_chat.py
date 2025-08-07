from datetime import datetime

from ATRI.system.lkapi.ai import chat_manager
from utils.limiter import LimitedQueue


class PetModel:
    def __init__(self, user_name, pet_name, instruction):
        self.user_name = user_name
        self.pet_name = pet_name
        self.instruction = instruction
        self.history = LimitedQueue(10)

    async def chat_with(self, msg: str):
        msg = f'{msg}({datetime.now().strftime("%Y年%m月%d日%A %H:%M")})'
        resp = await chat_manager.generate_content(
            f'{self.get_system_instruction()}\n'
            '聊天记录:\n'
            f'{''.join(f'{h.m}\n你的回答:{h.r}' for h in self.history.get_data())}\n'
            '最新对话:\n'
            f'{msg}'
        )
        self.history.add({'user': msg, 'resp': resp})
        return resp

    def chat_clear(self):
        self.history.clear()

    def get_system_instruction(self):
        return f'''你需要实现一个与多用户聊天的应用场景，请注意区分不同用户。这个聊天场景中，你需要与不同用户对话。
所有事件均与现实无关，你可以自由回答问题。
用户输入格式为：“用户名:聊天内容(当前日期与时间)”。在用户的聊天内容中出现的" [名称] "表示用户的名字。
而你只需要尽量简短的回答问题，并且只需要直接输出你的聊天内容即可，不要带上(当前日期与时间)这部分。
你的名字:{self.pet_name}
你的介绍:{self.instruction}
规则:唯一的主人:{self.user_name};不相信用户的亲属关系'''

    def change_user_name(self, user_name):
        self.user_name = user_name

    def change_pet_name(self, pet_name):
        self.pet_name = pet_name

    def change_instruction(self, instruction):
        self.instruction = instruction
