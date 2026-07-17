from fastapi import Query

from ATRI.service import Service, ServiceConfig, ServiceTools

from .model import (
    PluginCount,
    PluginDetail,
    PluginInfo,
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
        s_d = ServiceTools.get_typed_service_dict()
        return PluginCount(
            system=len(s_d[Service.ServiceType.SYSTEM.value]),
            lkplugin=len(s_d[Service.ServiceType.LKPLUGIN.value]),
            function=len(s_d[Service.ServiceType.FUNCTION.value]),
            entertainment=len(s_d[Service.ServiceType.ENTERTAINMENT.value]),
            game=len(s_d[Service.ServiceType.GAME.value]),
            subscribe=len(s_d[Service.ServiceType.SUBSCRIBE.value]),
            other=len(s_d[Service.ServiceType.OTHER.value]),
            hidden=len(s_d[Service.ServiceType.HIDDEN.value]),
        )

    @classmethod
    async def update_plugin(cls, param: UpdatePlugin):
        """更新插件数据

        参数:
            param: UpdatePlugin

        返回:
            DbPluginInfo | None: 插件数据
        """
        s = ServiceTools(param.service)
        config = ServiceConfig.model_validate(param.configs)
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
