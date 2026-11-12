"""权限默认值与显示名。

权限的**默认行为来自每个功能自己声明的作用域**（``FunctionCalling.scope`` /
``scope_by_action``），而不是一张按等级列出的功能清单。``permission`` 表只保存
对默认行为的**覆盖**：留空表示「按功能自己的声明执行」，填了才表示要收紧或放宽。

本模块只放纯数据，且位于 ``agent`` 包之外，供 ``config.py`` 在类定义期引用，
避免通过 ``agent/__init__.py`` 反向导入正在初始化的配置模块。
"""

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
