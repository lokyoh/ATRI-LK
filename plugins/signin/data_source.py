from ATRI.message import (
    InlineKeyboardRow,
    MessageBuilder,
    QQMessage,
)
from ATRI.system.help.qq import BTN_MENU
from ATRI.system.lkapi.entity.user import get_user_data, sign

from .qq import BTN_SIGNIN


class Signin:
    @staticmethod
    async def signin(event, matcher):
        user_id = event.get_user_id()
        message = MessageBuilder().text("")
        check, msg = sign(user_id)
        if check:
            message.text("\n签到成功:")
        for m in msg:
            message.auto_append(m)
        await matcher.finish(message, at_sender=True)


class QQSignin:
    @staticmethod
    async def signin(event, matcher):
        user_id = event.get_user_id()
        message = MessageBuilder()
        _, msg = sign(user_id)
        user_data = get_user_data(user_id)
        message.text(f"# {user_data.name}签到成功:")
        message.text(f"你已签到{user_data.signdays}天")
        message.text(f"- 等级: {user_data.lvl}\n")
        message.text(f"- 经验: {user_data.left_exp} / {user_data.get_lvl_exp()}")
        message.text(f"- ATRI币: {user_data.money}")
        message.text(f"- 好感: {user_data.love}")
        for m in msg:
            message.auto_append(f"- {m}")
        msg = QQMessage.md_msg(message.done())
        kb = QQMessage.keyboard(
            content=InlineKeyboardRow(buttons=[BTN_SIGNIN, BTN_MENU])
        )
        await matcher.send(msg + kb)


class SigninManager:
    def __init__(self):
        self.c = Signin

    def change_signin(self, c_s: type[Signin]):
        self.c = c_s

    async def signin(self, event, matcher):
        await self.c.signin(event, matcher)


signin = SigninManager()
