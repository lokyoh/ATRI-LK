"""LK 账户物品定义：肉鸽入场券。

肉鸽入场券是 **LK 背包物品**（不是 lkpet 自己的数据表）：签到发券、入场时自动扣券，
都由 ``ATRI.system.lkapi`` 的物品与签到事件体系承载，这样玩家能在 LK 背包里直接看到它。

物品必须在 ``item_loading_events`` 触发时注册；本模块的 :func:`register_rogue_items`
就是这个监听器，由 ``handlers/events.py`` 挂到事件总线。
"""

from ATRI.system.lkapi.entity.item import Item, ItemType, items

ROGUE_TICKET_ITEM = "肉鸽入场券"
"""入场券在 LK 物品表与玩家背包里使用的默认名称（对外可见，勿随意改名）。"""


def rogue_ticket_name() -> str:
    """当前生效的入场券名称（可在插件设置 ``rogue_ticket_item`` 里改）。"""
    try:
        from . import config as plugin_config

        configured = plugin_config.get("rogue_ticket_item", "")
    except Exception:  # pragma: no cover - 插件设置未就绪时的兜底
        configured = ""
    return str(configured) or ROGUE_TICKET_ITEM


def register_rogue_items() -> None:
    """注册肉鸽相关物品（``item_loading_events`` 监听器，可重复执行）。"""
    name = rogue_ticket_name()
    if items.has_item(name):
        return
    items.register(
        Item(
            name,
            ItemType.PROP,
            "肉鸽爬塔的入场凭证。持有它时发送「/宠物.肉鸽 开始」会自动消耗，"
            "当次入场不消耗行动点。每日签到可获得。",
            0,
        )
    )
