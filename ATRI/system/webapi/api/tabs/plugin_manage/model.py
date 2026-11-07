from typing import Any, Literal

from pydantic import BaseModel, Field, RootModel

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


class ServiceTypeInfo(BaseModel):
    """
    服务类型元信息（由 ``Service.ServiceType`` 动态生成）
    """

    key: str
    """稳定键，如 system / lkplugin"""
    name: str
    """枚举名，如 SYSTEM"""
    value: str
    """展示名，如 系统服务"""


class PluginCount(RootModel[dict[str, int]]):
    """
    各服务类型的插件数量

    键为 ``ServiceType.key``（枚举名小写）。新增服务类型时自动出现新键，
    不再需要改动本模型或前端。
    """


class PluginTypeList(RootModel[list[ServiceTypeInfo]]):
    """
    服务类型列表（顺序与 ``Service.ServiceType`` 定义顺序一致）
    """


class PluginDetail(PluginInfo):
    """
    插件详情
    """

    configs: ServiceConfig


class PluginRequest(BaseModel):
    service: str
    """插件服务名"""
    force: bool = False
    """强制更新：忽略「已是最新」的判断重新下载"""


class InstallDependenciesPayload(BaseModel):
    """
    安装依赖
    """

    handle_type: Literal["install", "uninstall"] = Field(..., description="处理类型")
    """处理类型"""

    dependencies: list[str] = Field(..., description="依赖列表")
    """依赖列表"""
