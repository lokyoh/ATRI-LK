from nonebot.exception import FinishedException

from ATRI.message import (
    InlineKeyboardRow,
    MessageBuilder,
    QQMessage,
)
from ATRI.system.agent.agent.history import log_bot_message
from ATRI.system.help.qq import BTN_MENU
from ATRI.system.lkapi.entity.user import get_user_data, sign

from .qq import BTN_SIGNIN


def sign_summary(user_id: str, signed: bool) -> str:
    """写进聊天历史的短摘要：只留谁签到的、第几天。"""
    try:
        user_data = get_user_data(user_id)
        name = user_data.name or str(user_id)
        days = user_data.signdays
    except Exception:
        return ""
    if not signed:
        return f"{name}今天已经签到过了"
    return f"{name}签到成功（第{days}天）"


class Signin:
    """签到的**兜底实现**。

    实际运行时会由 `core_signin` 通过 `change_signin(CoreSignin)` 替换掉，
    但保留一份可用实现，避免导入顺序变化时签到直接失效。
    这里同样记录历史，保持与 CoreSignin 行为一致。
    """

    @staticmethod
    async def signin(event, matcher):
        user_id = event.get_user_id()
        group_id = str(getattr(event, "group_id", None))
        pending: list[str] = []
        try:
            message = MessageBuilder().text("")
            check, msg = sign(user_id)
            if check:
                message.text("\n签到成功:")
            for m in msg:
                message.auto_append(m)
            summary = sign_summary(user_id, check)
            if summary:
                pending.append(summary)
            await matcher.finish(message, at_sender=True)
        except FinishedException:
            for text in pending:
                log_bot_message(group_id, text)
            raise


class QQSignin:
    @staticmethod
    async def signin(event, matcher):
        user_id = event.get_user_id()
        message = MessageBuilder()
        signed, msg = sign(user_id)
        user_data = get_user_data(user_id)
        message.text(f"# {user_data.name}签到成功:")
        message.text(f"你已签到{user_data.signdays}天")
        message.text(f"- 等级: {user_data.lvl}\n")
        message.text(f"- 经验: {user_data.left_exp} / {user_data.get_lvl_exp()}")
        message.text(f"- ATRI币: {user_data.money}")
        message.text(f"- 好感: {user_data.love}")
        for m in msg:
            message.auto_append(f"- {m}")
        md = QQMessage.md_msg(message.done())
        kb = QQMessage.keyboard(
            content=InlineKeyboardRow(buttons=[BTN_SIGNIN, BTN_MENU])
        )
        await matcher.send(md + kb)
        log_bot_message(getattr(event, "group_id", None), sign_summary(user_id, signed))


class SigninManager:
    def __init__(self):
        self.c = Signin

    def change_signin(self, c_s: type[Signin]):
        self.c = c_s

    async def signin(self, event, matcher):
        await self.c.signin(event, matcher)


signin = SigninManager()
