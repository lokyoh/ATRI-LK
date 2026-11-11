from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import PokeNotifyEvent
from nonebot.exception import ActionFailed as NoneBotActionFailed

PLAIN_TEXT_LIMIT = 500


def get_group_id(event: Event) -> str | None:
    """取群或频道标识；私聊返回 ``None``。"""
    for attr in ("group_id", "group_openid", "channel_id"):
        value = getattr(event, attr, None)
        if value:
            return str(value)
    return None


def get_plain_text(event: Event) -> str:
    """取事件里的纯文本，失败或过长时退化为事件描述。"""
    try:
        text = event.get_plaintext()
    except Exception:
        text = event.get_event_description()
    return (text or "")[:PLAIN_TEXT_LIMIT]


def is_poke(event: Event) -> bool:
    """是否为 OneBot v11 的戳一戳事件。"""
    return isinstance(event, PokeNotifyEvent)


def is_action_failed(exception: BaseException) -> bool:
    """是否为适配器的发送失败。

    OneBot v11 与 QQ 官方各有自己的 ``ActionFailed``，但都继承 NoneBot 的
    基类；再留一层类名判断，避免适配器把异常包成别的子类。
    """
    if isinstance(exception, NoneBotActionFailed):
        return True
    return any(cls.__name__ == "ActionFailed" for cls in type(exception).__mro__)
