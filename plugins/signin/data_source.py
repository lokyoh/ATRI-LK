from typing import Type

from ATRI.message import MessageBuilder
from ATRI.system.lkapi.entity.user import sign

class Signin:
    @staticmethod
    async def signin(event, matcher):
        user_id = event.get_user_id()
        message = MessageBuilder().text('')
        check, msg = sign(user_id)
        if check:
            message.text(f'\n签到成功:')
        for m in msg:
            message.auto_append(m)
        await matcher.finish(message, at_sender=True)


class SigninManager:
    def __init__(self):
        self.c = Signin

    def change_signin(self, c_s: Type[Signin]):
        self.c = c_s

    async def signin(self, event, matcher):
        await self.c.signin(event, matcher)


signin = SigninManager()
