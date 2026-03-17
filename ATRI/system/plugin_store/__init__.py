from nonebot.adapters.onebot.v11 import Message
from nonebot.params import CommandArg

from ATRI.exceptions import PluginError
from ATRI.message import MessageBuilder
from ATRI.permission import MASTER
from ATRI.service import Service, ServiceTools

from .data_source import PluginManager

plugin = Service(
    "插件商店", "插件商店", "0.4.0", Service.ServiceType.SYSTEM
).permission(MASTER)

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
        version = plugin_list[_plugin]["version"]
        if _plugin in ServiceTools.service_list:
            if version == "github":
                r = await PluginManager.check_github_plugin_update(plugin_list[_plugin]['repo'])
                if r["has_update"]:
                    install = "需更新"
                    version = f"{r["local_version"]}->" + r["remote_version"]
                else:
                    install = "已安装"
            else:
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
    if plugin_name not in plugin_list:
        await plugin_info.finish(f"找不到插件 {plugin_name}")
    _plugin = plugin_list[plugin_name]
    install = "未安装"
    version = _plugin["version"]
    if plugin_name in ServiceTools.service_list:
        if version == "github":
            r = await PluginManager.check_github_plugin_update(_plugin['repo'])
            if r["has_update"]:
                install = "需更新"
            else:
                install = "已安装"
            version = r["local_version"]
        else:
            now_version = ServiceTools(plugin_name).load_service().version
            if now_version != version:
                install = "需更新"
            else:
                install = "已安装"
    message = (
        MessageBuilder()
        .text(f"{plugin_name} [{install}]")
        .text(f"版本:{version}")
        .text(f"作者:{_plugin['author']}")
        .text(f"介绍:{_plugin['docs']}")
    )
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
    if plugin_name in ServiceTools.service_list:
        await add.finish(f"插件 {plugin_name} 已经安装")
    try:
        await PluginManager.install_plugin(plugin_name, False)
        await add.finish(
            f"{plugin_name}安装成功,需重启才生效"
        )
    except PluginError as e:
        await add.finish(e.prompt)
    except Exception:
        raise


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
    except Exception:
        raise


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
    if plugin_name not in plugin_list:
        await update.finish(f"找不到插件 {plugin_name}")
    version = plugin_list[plugin_name]["version"]
    if plugin_name in ServiceTools.service_list:
        if version == "github":
            r = await PluginManager.check_github_plugin_update(plugin_list[plugin_name]['repo'])
            if not r["has_update"]:
                await update.finish(f"{plugin_name} 无需更新")
        if ServiceTools(plugin_name).load_service().version == version:
            await update.finish(f"{plugin_name} 无需更新")
    try:
        _plugin = PluginManager.plugin_list[plugin_name]
        if repo := _plugin.get("repo", None):
            await PluginManager.update_github_plugin(repo)
        else:
            await PluginManager.install_plugin(plugin_name)
        await update.finish(f"{plugin_name}-{version}安装成功，请重启以启用新版插件")
    except PluginError as e:
        await update.finish(e.prompt)
    except Exception:
        raise


check_update = plugin.on_command("检查插件更新", "检查所有的插件的更新")


@check_update.handle()
async def _():
    try:
        plugin_list = await PluginManager.get_plugin_list()
        await PluginManager.check_list()
    except PluginError:
        await plugin_info.finish("获取插件信息失败")
    message = MessageBuilder().text("需要更新的插件:")
    for plugin_name in ServiceTools.service_list:
        if plugin_name in plugin_list:
            version = plugin_list[plugin_name]["version"]
            if version == "github":
                r = await PluginManager.check_github_plugin_update(plugin_list[plugin_name]['repo'])
                if r["has_update"]:
                    message.text(f"{plugin_name} {r["local_version"]}->{r["remote_version"]}")
            else:
                now_version = ServiceTools(plugin_name).load_service().version
                if now_version != version:
                    message.text(f"{plugin_name} {now_version}->{version}")
    await update_all.finish(message)


update_all = plugin.on_command("更新所有插件", "更新所有的插件")


@update_all.handle()
async def _():
    try:
        plugin_list = await PluginManager.get_plugin_list()
        await PluginManager.check_list()
    except PluginError:
        await plugin_info.finish("获取插件信息失败")
    message = MessageBuilder().text("更新情况(更新完成后请重启):")
    for plugin_name in ServiceTools.service_list:
        if plugin_name in plugin_list:
            version = plugin_list[plugin_name]["version"]
            try:
                if version == "github":
                    repo = plugin_list[plugin_name]['repo']
                    r = await PluginManager.check_github_plugin_update(repo)
                    if r["has_update"]:
                        await PluginManager.update_github_plugin(repo)
                        message.text(f"{plugin_name}-{version}安装成功")
                else:
                    if ServiceTools(plugin_name).load_service().version != version:
                        await PluginManager.install_plugin(plugin_name)
                        message.text(f"{plugin_name}-{version}安装成功")
            except PluginError as e:
                message.text(e.prompt)
            except Exception as e:
                message.text(str(e))
    await update_all.finish(message)
