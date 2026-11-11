from dataclasses import dataclass

from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher

from ATRI.permission import is_master_user


@dataclass(frozen=True)
class AuthSnapshot:
    bot_id: str
    platform: str
    user_id: str
    group_id: str
    is_master: bool
    service: str | None
    matcher_label: str | None

    @property
    def target(self) -> str:
        return self.group_id or self.user_id or "-"

    def describe(self) -> str:
        return (
            f"service={self.matcher_label or '-'}, bot_id={self.bot_id}, "
            f"user_id={self.user_id or '-'}, group_id={self.group_id or '-'}"
        )


def event_user_id(event: Event) -> str:
    """取事件主体 id。

    OneBot v11 的事件直接有 ``user_id``；QQ 官方适配器只有 ``author``，
    需要退回适配器实现的 :meth:`Event.get_user_id`（群消息是 member_openid）。
    """
    user_id = str(getattr(event, "user_id", "") or "")
    if user_id:
        return user_id
    try:
        return str(event.get_user_id() or "")
    except Exception:
        return ""


def event_group_id(event: Event) -> str:
    return str(getattr(event, "group_id", "") or "")


def build_snapshot(event: Event, bot: Bot, matcher: Matcher) -> AuthSnapshot:
    bot_id = str(bot.self_id)
    user_id = event_user_id(event)
    group_id = event_group_id(event)
    service = getattr(matcher, "service_name", None) or None
    module_name = getattr(matcher, "module_name", None)
    return AuthSnapshot(
        bot_id=bot_id,
        platform=str(getattr(bot, "type", "") or ""),
        user_id=user_id,
        group_id=group_id,
        is_master=bool(user_id) and is_master_user(user_id),
        service=service,
        matcher_label=service or module_name,
    )
