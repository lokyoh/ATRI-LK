from enum import Enum


class BlockReason(str, Enum):
    """事件被拦截的原因码。

    统一使用稳定的英文字符串，便于日志检索、测试断言和后续 WebUI 展示。
    """

    GLOBAL_DISABLED = "global_disabled"
    BOT_DISABLED = "bot_disabled"
    USER_BANNED = "user_banned"
    USER_NOT_ALLOWED = "user_not_allowed"
    GROUP_BANNED = "group_banned"
    GROUP_NOT_ALLOWED = "group_not_allowed"
    GROUP_DISABLED = "group_disabled"
    USER_LEVEL_TOO_LOW = "user_level_too_low"

    SERVICE_DISABLED = "service_disabled"
    SERVICE_USER_DISABLED = "service_user_disabled"
    SERVICE_GROUP_DISABLED = "service_group_disabled"
    SERVICE_GROUP_NOT_IN_WHITELIST = "service_group_not_in_whitelist"

    SERVICE_BLOCKED_TYPE = "service_blocked_type"
    SERVICE_PARENT_DISABLED = "service_parent_disabled"
    SERVICE_CD = "service_cd"
    SERVICE_LIMIT = "service_limit"
    SERVICE_BUSY = "service_busy"

    MALICIOUS_BAN = "malicious_ban"
