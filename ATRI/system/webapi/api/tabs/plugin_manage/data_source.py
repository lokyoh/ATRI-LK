from fastapi import Query

from ATRI.service import Service, ServiceConfig, ServiceTools

from .model import (
    PluginCount,
    PluginDetail,
    PluginInfo,
    PluginTypeList,
    ServiceTypeInfo,
    UpdatePlugin,
)


class ApiDataSource:
    @classmethod
    async def get_plugin_list(cls, plugin_type: list = Query(None)) -> list[PluginInfo]:
        """获取插件列表

        参数:
            plugin_type: 插件类型.
            menu_type: 菜单类型.

        返回:
            list[PluginInfo]: 插件数据列表
        """
        plugin_list: list[PluginInfo] = []
        plugins = ServiceTools.service_list.values()
        for i, plugin in enumerate(plugins):
            s_i = plugin.get_info()
            if s_i.type not in plugin_type:
                continue
            plugin_info = PluginInfo(
                id=i,
                plugin_name=s_i.service,
                docs=s_i.docs,
                version=s_i.version if plugin.get_info() else "Unknown",
                type=s_i.type,
                author=s_i.author,
                status=plugin.conf().enabled,
                is_builtin=s_i.service in ServiceTools.builtin_plugins,
                allow_setting=plugin.plugin_config() is not None,
                allow_switch=s_i.allow_switch,
            )
            plugin_list.append(plugin_info)
        return plugin_list

    @classmethod
    async def get_plugin_count(cls) -> PluginCount:
        """按 ``Service.ServiceType`` 动态统计各类型插件数量。

        键取 ``ServiceType.key``（枚举名小写），新增服务类型时自动出现新键。
        ``CLOSED`` 是“已关闭”的展示态而非注册类型，不参与统计（与
        ``ServiceTools.get_typed_service_dict`` 的口径一致）。
        """
        s_d = ServiceTools.get_typed_service_dict()
        counts = {
            s_t.key: len(s_d.get(s_t.value, []))
            for s_t in Service.ServiceType
            if s_t != Service.ServiceType.CLOSED
        }
        return PluginCount.model_validate(counts)

    @classmethod
    async def get_plugin_types(cls) -> PluginTypeList:
        """返回服务类型列表，供 WebUI 动态生成类型标签页。"""
        types = [
            ServiceTypeInfo(key=s_t.key, name=s_t.name, value=s_t.value)
            for s_t in Service.ServiceType
            if s_t != Service.ServiceType.CLOSED
        ]
        return PluginTypeList.model_validate(types)

    @classmethod
    async def update_plugin(cls, param: UpdatePlugin):
        """更新插件数据

        参数:
            param: UpdatePlugin

        返回:
            DbPluginInfo | None: 插件数据
        """
        s = ServiceTools(param.service)
        # 以磁盘上的现有配置为基底合并，避免前端只提交部分字段时
        # 把新增的策略字段（CD、次数、撤回延迟等）重置为默认值。
        merged = s.load_service_config().model_dump()
        if param.configs:
            merged.update(param.configs)
        config = ServiceConfig.model_validate(merged)
        s.save_service_config(config)
        return None

    @classmethod
    async def get_plugin_detail(cls, service: str) -> PluginDetail:
        """获取插件详情

        参数:
            service: 模块名

        异常:
            ValueError: 插件不存在

        返回:
            PluginDetail: 插件详情数据
        """
        if service not in ServiceTools.service_list:
            raise ValueError("插件不存在")
        s = ServiceTools.service_list[service]
        info = s.get_info()
        configs = s.conf()
        return PluginDetail(
            id=list(ServiceTools.service_list.keys()).index(service),
            plugin_name=info.service,
            docs=info.docs,
            version=info.version,
            type=info.type,
            author=info.author,
            status=configs.enabled,
            is_builtin=info.service in ServiceTools.builtin_plugins,
            allow_switch=info.allow_switch,
            allow_setting=s.plugin_config() is not None,
            configs=configs,
        )
