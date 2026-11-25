from __future__ import annotations

from typing import Any

#: 等级显示名。运行时会优先使用配置里的 ``label``。
LEVEL_LABELS: dict[str, str] = {
    "master": "主人",
    "admin": "管理",
    "user": "用户",
    "system": "系统",
}

#: 三个固定分类的默认值：只有显示名，没有默认 allow/deny。
#:
#: allow / deny 是**覆盖项**，默认为空——真正的默认权限由功能类自己的
#: ``scope`` 声明。这样改名、新增功能都不需要同步维护一张默认清单。
DEFAULT_PERMISSION_OVERRIDES: dict[str, dict[str, Any]] = {
    "master": {
        "label": "主人",
        "allow": [],
        "deny": [],
    },
    "admin": {
        "label": "管理",
        "allow": [],
        "deny": [],
    },
    "user": {
        "label": "用户",
        "allow": [],
        "deny": [],
    },
}

#: 个人权限覆盖的默认条目结构。仅用于 UI / 文档，不作为真实用户配置。
DEFAULT_USER_PERMISSION_ITEM: dict[str, Any] = {
    "downgrade_to": "",
    "allow": [],
    "deny": [],
}

__all__ = [
    "DEFAULT_PERMISSION_OVERRIDES",
    "DEFAULT_USER_PERMISSION_ITEM",
    "LEVEL_LABELS",
]
