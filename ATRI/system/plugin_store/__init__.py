from nonebot.adapters.onebot.v11 import Message
from nonebot.params import CommandArg

from ATRI.service import Service, service_list, ServiceTools
from ATRI.permission import MASTER
from ATRI.message import MessageBuilder
from ATRI.exceptions import PluginError

from .data_source import PluginManager

plugin = Service("插件商店").document("插件商店").type(Service.ServiceType.SYSTEM).version("0.1.0").permission(MASTER)

plugins = plugin.on_command("插件列表", "查看插件列表")


@plugins.handle()
async def _():
    plugin_list = await PluginManager.get_plugin_list()
    i = 1
    j = 0
    info = "插件列表:\n"
    num = len(plugin_list)
    for _plugin in plugin_list.keys():
        if i - j * 20 > 20:
            info += f"第{j + 1}页 共{i - 1}/{num}个"
            await plugins.send(info)
            j += 1
            info = ""
        install = "未安装"
        version = plugin_list[_plugin]['version']
        if _plugin in service_list:
            now_version = ServiceTools(_plugin).load_service().version
            if now_version != version:
                install = "需更新"
                version = f"{now_version}->" + version
            else:
                install = "已安装"
        info += f"{i}.[{install}]{_plugin} {version}\n"
        i += 1
    info += f"第{j + 1}页 共{i - 1}/{num}个"
    await plugins.send(info)


plugin_info = plugin.on_command("插件详情", "查看指定插件详情")


@plugin_info.handle()
async def _(args: Message = CommandArg()):
    try:
        plugin_list = await PluginManager.get_plugin_list()
        await PluginManager.check_list()
    except PluginError:
        await plugin_info.finish("获取插件信息失败")
    plugin_name = args.extract_plain_text().replace(" ", "")
    if not plugin_name:
        await plugin_info.finish("请输入插件名")
    if not plugin_name in plugin_list:
        await plugin_info.finish(f"找不到插件 {plugin_name}")
    _plugin = plugin_list[plugin_name]
    install = "未安装"
    version = _plugin['version']
    if plugin_name in service_list:
        now_version = ServiceTools(plugin_name).load_service().version
        if now_version != version:
            install = "需更新"
        else:
            install = "已安装"
    message = (MessageBuilder()
               .text(f"{plugin_name} [{install}]")
               .text(f"版本:{version}")
               .text(f"作者:{_plugin['author']}")
               .text(f"介绍:{_plugin['docs']}"))
    await plugin_info.finish(message)


add = plugin.on_command("添加插件", "添加指定插件")


@add.handle()
async def _(args: Message = CommandArg()):
    try:
        await PluginManager.check_list()
    except PluginError:
        await plugin_info.finish("获取插件信息失败")
    plugin_name = args.extract_plain_text().replace(" ", "")
    if not plugin_name:
        await add.finish("请输入插件名")
    if plugin_name in service_list:
        await add.finish(f"插件 {plugin_name} 已经安装")
    try:
        await PluginManager.install_plugin(plugin_name, True)
        await add.finish(f"{plugin_name}安装成功")
    except PluginError as e:
        await add.finish(e.prompt)
    except Exception as e:
        raise e


remove = plugin.on_command("移除插件", "移除指定插件")


@remove.handle()
async def _(args: Message = CommandArg()):
    try:
        await PluginManager.check_list()
    except PluginError:
        await plugin_info.finish("获取插件信息失败")
    plugin_name = args.extract_plain_text().replace(" ", "")
    if not plugin_name:
        await remove.finish("请输入插件名")
    try:
        await PluginManager.remove_plugin(plugin_name)
        await remove.finish(f"{plugin_name}移除成功，请重启生效")
    except PluginError as e:
        await remove.finish(e.prompt)
    except Exception as e:
        raise e


update = plugin.on_command("更新插件", "更新指定插件")


@update.handle()
async def _(args: Message = CommandArg()):
    try:
        plugin_list = await PluginManager.get_plugin_list()
        await PluginManager.check_list()
    except PluginError:
        await plugin_info.finish("获取插件信息失败")
    plugin_name = args.extract_plain_text().replace(" ", "")
    if not plugin_name:
        await update.finish("请输入插件名")
    if not plugin_name in plugin_list:
        await update.finish(f"找不到插件 {plugin_name}")
    version = plugin_list[plugin_name]["version"]
    if plugin_name in service_list:
        if ServiceTools(plugin_name).load_service().version == version:
            await update.finish(f"{plugin_name} 无需更新")
    try:
        await PluginManager.install_plugin(plugin_name)
        await update.finish(f"{plugin_name}-{version}安装成功，请重启以启用新版插件")
    except PluginError as e:
        await update.finish(e.prompt)
    except Exception as e:
        raise e


check_update = plugin.on_command("/检查插件更新", "检查所有的插件的更新")


@check_update.handle()
async def _():
    try:
        plugin_list = await PluginManager.get_plugin_list()
        await PluginManager.check_list()
    except PluginError:
        await plugin_info.finish("获取插件信息失败")
    message = MessageBuilder().text("需要更新的插件:")
    for plugin_name in service_list:
        if plugin_name in plugin_list:
            version = plugin_list[plugin_name]["version"]
            now_version = ServiceTools(plugin_name).load_service().version
            if now_version != version:
                message.text(f"{plugin_name} {now_version}->{version}")
    await update_all.finish(message)


update_all = plugin.on_command("/更新所有插件", "更新所有的插件")


@update_all.handle()
async def _():
    try:
        plugin_list = await PluginManager.get_plugin_list()
        await PluginManager.check_list()
    except PluginError:
        await plugin_info.finish("获取插件信息失败")
    message = MessageBuilder().text("更新情况(更新完成后请重启):")
    for plugin_name in service_list:
        if plugin_name in plugin_list:
            version = plugin_list[plugin_name]["version"]
            if ServiceTools(plugin_name).load_service().version != version:
                try:
                    await PluginManager.install_plugin(plugin_name)
                    message.text(f"{plugin_name}-{version}安装成功")
                except PluginError as e:
                    message.text(e.prompt)
                except Exception as e:
                    message.text(str(e))
    await update_all.finish(message)
