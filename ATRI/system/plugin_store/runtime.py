import contextlib
import importlib
import sys

import nonebot

from ATRI import service
from ATRI.exceptions import PluginError
from ATRI.log import log


class PluginRuntimeManager:
    """插件运行时管理器，负责热重载已加载的插件模块。"""

    @staticmethod
    def _clear_nonebot_plugin_registry(module_name: str):
        """清理 NoneBot 中已经残留的插件注册信息，避免重复加载。"""
        nb_plugin = getattr(nonebot, "plugin", None)
        if nb_plugin is None:
            return
        plugin_module_name = module_name.rsplit(".", 1)[-1]
        stale_plugin_ids = []
        for plugin_id, plugin in list(getattr(nb_plugin, "_plugins", {}).items()):
            if (
                plugin.module_name == module_name
                or plugin.module_name.startswith(f"{module_name}.")
                or plugin.name == plugin_module_name
                or plugin.id_ == plugin_module_name
                or plugin.id_.startswith(f"{plugin_module_name}:")
            ):
                stale_plugin_ids.append(plugin_id)
        for plugin_id in stale_plugin_ids:
            plugin = nb_plugin._plugins.get(plugin_id)
            if plugin is not None:
                nb_plugin._revert_plugin(plugin)
        for manager in list(getattr(nb_plugin, "_managers", [])):
            removed_ids = []
            for plugin_id, controlled_module in list(
                manager.controlled_modules.items()
            ):
                if controlled_module == module_name or controlled_module.startswith(
                    f"{module_name}."
                ):
                    removed_ids.append(plugin_id)
            for plugin_id in removed_ids:
                if plugin_id in getattr(manager, "_third_party_plugin_ids", {}):
                    del manager._third_party_plugin_ids[plugin_id]
                if plugin_id in getattr(manager, "_searched_plugin_ids", {}):
                    del manager._searched_plugin_ids[plugin_id]
            if not manager.controlled_modules:
                with contextlib.suppress(ValueError):
                    nb_plugin._managers.remove(manager)

    @staticmethod
    def resolve_plugin_name(plugin_name: str) -> str:
        s = service.ServiceTools.get_service(plugin_name)
        if s is None:
            raise PluginError(f"没有发现插件: {plugin_name}")
        return s.module_name

    @staticmethod
    async def unload_plugin(plugin_name: str):
        module_name = PluginRuntimeManager.resolve_plugin_name(plugin_name)
        s = service.ServiceTools.get_service(plugin_name)
        if s:
            await s.unload()
        PluginRuntimeManager._clear_nonebot_plugin_registry(module_name)
        for key in list(sys.modules):
            if key == module_name or key.startswith(f"{module_name}."):
                del sys.modules[key]
        service.ServiceTools.service_list.pop(plugin_name, None)
        log.info(f"已卸载插件模块：{module_name}")

    @staticmethod
    async def reload_plugin(plugin_name: str):
        module_name = PluginRuntimeManager.resolve_plugin_name(plugin_name)
        await PluginRuntimeManager.unload_plugin(plugin_name)
        nonebot.load_plugin(module_name)
        module = importlib.import_module(module_name)
        log.success(f"已热重载插件：{module_name}")
        return module
