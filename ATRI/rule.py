from nonebot.adapters import Bot, Event
from nonebot.rule import Rule


def to_bot() -> Rule:
    async def _to_bot(bot: Bot, event: Event) -> bool:
        return event.is_tome()

    return Rule(_to_bot)

def check_event_type(event_type: type[Event] | tuple[type[Event], ...]) -> Rule:
    async def check(event: Event) -> bool:
        return isinstance(event, event_type)

    return Rule(check)
