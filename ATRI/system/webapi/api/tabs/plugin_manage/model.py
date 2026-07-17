from typing import Any, Literal

from pydantic import BaseModel, Field

from ATRI.service import ServiceConfig


class PluginSwitch(BaseModel):
    """
    插件开关
    """

    service: str
    """模块"""
    status: bool
    """开关状态"""


class UpdatePlugin(BaseModel):
    """
    插件修改参数
    """

    service: str
    """模块"""
    configs: dict[str, Any] | None = None
    """设置项"""


class PluginInfo(BaseModel):
    """
    基本插件信息
    """

    id: int
    """插件id"""
    plugin_name: str
    """插件名称"""
    docs: str
    """描述"""
    version: str
    """版本"""
    type: str
    """类型"""
    author: str | None = None
    """作者"""
    status: bool
    """状态"""
    is_builtin: bool = False
    """是否为内置插件"""
    allow_switch: bool = True
    """是否允许开关"""
    allow_setting: bool = False
    """是否允许设置"""


class PluginCount(BaseModel):
    """
    插件数量
    """

    system: int = 0
    """系统服务"""
    lkplugin: int = 0
    """LK扩展服务"""
    function: int = 0
    """功能性服务"""
    entertainment: int = 0
    """娱乐服务"""
    game: int = 0
    """游戏服务"""
    subscribe: int = 0
    """订阅服务"""
    other: int = 0
    """其他服务"""
    hidden: int = 0
    """隐藏服务"""


class PluginDetail(PluginInfo):
    """
    插件详情
    """

    configs: ServiceConfig


class PluginRequest(BaseModel):
    service: str
    """插件服务名"""


class InstallDependenciesPayload(BaseModel):
    """
    安装依赖
    """

    handle_type: Literal["install", "uninstall"] = Field(..., description="处理类型")
    """处理类型"""

    dependencies: list[str] = Field(..., description="依赖列表")
    """依赖列表"""
