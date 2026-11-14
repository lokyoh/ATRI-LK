from __future__ import annotations

from typing import Any

from ATRI.configs import Field, PluginConfig
from ATRI.log import log

#: 单个群每小时的主动发言默认上限（Agent 侧未配置时使用）。
DEFAULT_MAX_PER_HOUR = 3

#: 单个群两次主动发言的最小间隔（秒）。
DEFAULT_COOLDOWN = 300


class LKChatConfig(PluginConfig):
    """聊天插件配置。

    proactively_chat 是总开关；proactive_chats 按「机器人 → 群号」两级分组，
    每个群有自己的开关与节流。两级都按 ID 精确匹配，查不到就是不接话。
    """

    chat_switch = Field(bool, False, "Agent聊天开关", "Agent聊天总开关,需和白名单配合")
    whit_list = Field(
        list, [], "聊天白名单", "只有在白名单的群才开启Agent聊天", item_type=str
    )
    proactively_chat = Field(
        bool,
        False,
        "主动聊天总开关",
        "关闭时任何群都不会主动接话，下面的按群配置一律不生效",
    )
    proactive_chats = Field(
        dict,
        {},
        "主动聊天（按群）",
        "按「机器人 ID → 群号」分组，为每个群单独设置是否接话与节流。"
        "两级都按 ID 精确匹配，不做通配；留空 = 任何群都不主动接话。",
        ui_type="category-collection",
        category_label="label",
        key_label="机器人 ID",
        allow_add=True,
        allow_remove=True,
        item_field=Field(
            dict,
            {},
            "机器人",
            children={
                "label": Field(str, "", "备注", "只用于界面显示，不影响行为"),
                "chats": Field(
                    dict,
                    {},
                    "群配置",
                    "群号与「聊天白名单」一致；不在其中的群不会主动接话",
                    ui_type="category-collection",
                    key_label="群号",
                    allow_add=True,
                    allow_remove=True,
                    item_field=Field(
                        dict,
                        {},
                        "群",
                        children={
                            "enabled": Field(
                                bool,
                                True,
                                "启用主动聊天",
                                "关闭后这个群只在被叫到时才回话",
                            ),
                            "max_per_hour": Field(
                                int,
                                DEFAULT_MAX_PER_HOUR,
                                "每小时上限",
                                "这个群每小时最多主动发言几次（被叫到不计）",
                                min=1,
                                max=60,
                            ),
                            "cooldown": Field(
                                int,
                                DEFAULT_COOLDOWN,
                                "冷却（秒）",
                                "两次主动发言之间的最小间隔",
                                min=0,
                                max=3600,
                            ),
                        },
                    ),
                ),
            },
        ),
    )

    def _migrate_saved(self, saved: dict) -> dict:
        """旧版白名单不再使用。

        旧配置只有一个全局开关和一张群列表，没有机器人维度；按群结构要求
        精确到机器人，无法猜测归属，因此只把总开关的值搬过来，群号记进日志
        提示用户重新添加。
        """
        legacy = saved.pop("proactively_chat_whit_list", None)
        if legacy:
            groups = [str(item).strip() for item in legacy if str(item).strip()]
            if groups:
                log.warning(
                    "聊天插件：旧版「主动聊天白名单」已不再使用，"
                    f"请在「主动聊天（按群）」里按机器人重新添加：{'、'.join(groups)}"
                )
        return saved


def _entry_chats(entry: Any) -> dict[str, Any]:
    """取一个机器人条目下的群字典，顺手修正类型。"""
    if not isinstance(entry, dict):
        return {}
    chats = entry.get("chats")
    if not isinstance(chats, dict):
        chats = entry["chats"] = {}
    return chats


def proactive_entry(bot_id: str, chat_id: str) -> dict[str, Any] | None:
    """取某个群的实际配置：机器人 ID 与群号都精确匹配，查不到返回 None。"""
    cfg = PluginConfig.get_config("聊天")
    if cfg is None:
        return None
    buckets = cfg.get("proactive_chats")
    if not isinstance(buckets, dict):
        return None
    key = str(bot_id or "").strip()
    chat = str(chat_id or "").strip()
    if not key or not chat:
        return None
    item = _entry_chats(buckets.get(key)).get(chat)
    return item if isinstance(item, dict) else None


def proactive_limits(bot_id: str, chat_id: str) -> tuple[int, float]:
    """这个群的上限与冷却；这个群没配置就用默认值（它本来也不会主动接话）。"""
    item = proactive_entry(bot_id, chat_id) or {}
    try:
        max_per_hour = int(item.get("max_per_hour", DEFAULT_MAX_PER_HOUR))
    except (TypeError, ValueError):
        max_per_hour = DEFAULT_MAX_PER_HOUR
    try:
        cooldown = float(item.get("cooldown", DEFAULT_COOLDOWN))
    except (TypeError, ValueError):
        cooldown = DEFAULT_COOLDOWN
    return max_per_hour, cooldown


def is_proactive_chat(bot_id: str, chat_id: str) -> bool:
    """总开关打开、且这个群自己启用了，才主动接话。"""
    cfg = PluginConfig.get_config("聊天")
    if cfg is None or not cfg.get("proactively_chat"):
        return False
    item = proactive_entry(bot_id, chat_id)
    return bool(item and item.get("enabled"))


def proactive_chat_ids() -> set[str]:
    """开启了主动接话的群号（不分机器人），供历史白名单使用。"""
    cfg = PluginConfig.get_config("聊天")
    if cfg is None or not cfg.get("proactively_chat"):
        return set()
    buckets = cfg.get("proactive_chats")
    if not isinstance(buckets, dict):
        return set()
    allowed: set[str] = set()
    for entry in buckets.values():
        for chat_id, item in _entry_chats(entry).items():
            if isinstance(item, dict) and item.get("enabled"):
                allowed.add(str(chat_id).strip())
    return allowed
