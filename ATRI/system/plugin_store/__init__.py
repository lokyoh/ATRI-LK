from nonebot.adapters.onebot.v11 import Message
from nonebot.exception import FinishedException
from nonebot.params import CommandArg

from ATRI.exceptions import PluginError
from ATRI.message import MessageBuilder
from ATRI.permission import MASTER
from ATRI.service import Service, ServiceTools

from .compat import PluginRequirement, lkapi_note
from .compat import check as check_compat
from .data_source import PluginEntry, PluginManager, PluginRuntimeManager

plugin = Service(
    "插件商店", "插件商店", "0.5.0", Service.ServiceType.SYSTEM
).permission(MASTER)

plugins = plugin.on_command("插件列表", "查看插件列表")


def _requirement_text(requirement: PluginRequirement) -> str:
    """把插件要求拼成一行可读文本，未声明时返回空串。"""
    parts = []
    if requirement.type:
        parts.append(f"种类:{requirement.type}")
    if requirement.framework:
        parts.append(f"框架:{requirement.framework}")
    if requirement.lkapi is not None:
        parts.append(f"lkapi:{requirement.lkapi}")
    return " ".join(parts)


def _compat_text(entry: PluginEntry) -> str:
    r = check_compat(entry.requirement)
    if r.status == "supported":
        return ""
    if r.status == "unknown":
        return "未知兼容性"
    return f"⚠{r.label}"


async def _install_state(plugin_name: str, version: str, repo: str = ""):
    """返回 (安装状态, 展示用版本号)，仓库型插件按 meta.yml 比对。"""
    if plugin_name not in ServiceTools.service_list:
        return "未安装", version
    try:
        if repo:
            r = await PluginManager.check_github_plugin_update(repo)
            if r["has_update"]:
                return "需更新", f"{r['local_version']}->{r['remote_version']}"
            return "已安装", version
        now_version = ServiceTools(plugin_name).load_service().version
        if now_version != version:
            return "需更新", f"{now_version}->{version}"
        return "已安装", version
    except PluginError:
        raise
    except Exception:
        return "已安装", version


@plugins.handle()
async def _():
    plugin_list = await PluginManager.get_plugin_list()
    i = 1
    j = 0
    info = "插件列表:\n"
    num = len(plugin_list)
    for _plugin in plugin_list:
        if i - j * 20 > 20:
            info += f"第{j + 1}页 共{i - 1}/{num}个"
            await plugins.send(info)
            j += 1
            info = ""
        entry = PluginEntry.from_mapping(_plugin, plugin_list[_plugin])
        install, version = await _install_state(_plugin, entry.version, entry.repo)
        line = f"{i}.[{install}]{_plugin} {version}"
        extra = _requirement_text(entry.requirement)
        if extra:
            line += f" | {extra}"
        warning = _compat_text(entry)
        if warning:
            line += f" {warning}"
        info += line + "\n"
        i += 1
    info += f"第{j + 1}页 共{i - 1}/{num}个"
    await plugins.send(info)


refresh = plugin.on_command("刷新插件列表", "从商店索引重新拉取插件列表")


@refresh.handle()
async def _():
    try:
        plugin_list = await PluginManager.refresh_list()
    except PluginError as e:
        await refresh.finish(f"刷新失败：{e.prompt}")
    except Exception as e:
        await refresh.finish(f"刷新失败：{type(e).__name__}: {e}")
    await refresh.finish(f"插件列表已刷新，共 {len(plugin_list)} 个插件")


plugin_info = plugin.on_command("插件详情", "查看指定插件详情")


@plugin_info.handle()
async def _(args: Message = CommandArg()):
    try:
        await PluginManager.get_plugin_list()
        await PluginManager.check_list()
    except PluginError:
        await plugin_info.finish("获取插件信息失败")
    plugin_name = args.extract_plain_text().replace(" ", "")
    if not plugin_name:
        await plugin_info.finish("请输入插件名")
    if plugin_name not in PluginManager.plugin_list:
        await plugin_info.finish(f"找不到插件 {plugin_name}")
    try:
        entry = await PluginManager.get_entry(plugin_name)
    except PluginError as e:
        await plugin_info.finish(e.prompt)
    install, version = await _install_state(plugin_name, entry.version, entry.repo)
    install = "√" + install if install != "未安装" else "×" + install
    message = (
        MessageBuilder()
        .text(f"{plugin_name} [{install}]")
        .text(f"版本:{version}")
        .text(f"作者:{entry.author}")
        .text(f"介绍:{entry.docs}")
    )
    compat = PluginManager.compat(entry)
    if entry.requirement.type:
        message.text(f"种类:{entry.requirement.type}")
    if entry.requirement.framework:
        message.text(f"基于框架:{entry.requirement.framework}")
    else:
        message.text("基于框架:未声明")
    if entry.requirement.lkapi is not None:
        message.text(f"基于lkapi:{entry.requirement.lkapi}")
    detail = (
        f"(当前框架{compat.framework or '未知'} "
        f"兼容范围{compat.supported} "
        f"lkapi{compat.lkapi if compat.lkapi is not None else '未知'})"
    )
    message.text(f"兼容性:{compat.label}{detail}")
    if compat.reason and compat.status not in ("unknown",):
        message.text(f"说明:{compat.reason}")
    note = lkapi_note(entry.requirement)
    if note:
        message.text(note)
    await plugin_info.finish(message)


add = plugin.on_command("添加插件", "添加指定插件")


@add.handle()
async def _(args: Message = CommandArg()):
    try:
        await PluginManager.check_list()
    except PluginError:
        await add.finish("获取插件信息失败")
    plugin_name = args.extract_plain_text().replace(" ", "")
    if not plugin_name:
        await add.finish("请输入插件名")
    if plugin_name in ServiceTools.service_list:
        await add.finish(f"插件 {plugin_name} 已经安装")
    try:
        await PluginManager.install_plugin(plugin_name, True)
        await add.finish(f"{plugin_name}安装成功")
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
        await remove.finish("获取插件信息失败")
    plugin_name = args.extract_plain_text().replace(" ", "")
    if not plugin_name:
        await remove.finish("请输入插件名")
    try:
        await PluginManager.remove_plugin(plugin_name)
        await remove.finish(f"{plugin_name}移除成功")
    except PluginError as e:
        await remove.finish(e.prompt)
    except Exception:
        raise


update = plugin.on_command("更新插件", "更新指定插件")


@update.handle()
async def _(args: Message = CommandArg()):
    try:
        await PluginManager.check_list()
    except PluginError:
        await update.finish("获取插件信息失败")
    raw = args.extract_plain_text().replace(" ", "")
    if not raw:
        await update.finish("请输入插件名")
    force = raw.endswith("-f")
    plugin_name = raw[:-2] if force else raw
    if not plugin_name:
        await update.finish("请输入插件名")
    _, msg = await PluginManager.update_plugin(plugin_name, force=force)
    await update.finish(msg)


check_update = plugin.on_command("检查插件更新", "检查所有的插件的更新")


@check_update.handle()
async def _():
    try:
        plugin_list = await PluginManager.refresh_list()
    except PluginError as e:
        await check_update.finish(f"获取插件信息失败：{e.prompt}")
    except Exception as e:
        await check_update.finish(f"获取插件信息失败：{type(e).__name__}: {e}")
    message = MessageBuilder().text("需要更新的插件:")
    for plugin_name in ServiceTools.service_list:
        if plugin_name in plugin_list:
            r = await PluginManager.check_update(plugin_name)
            if r:
                message.text(f"{plugin_name} {r[0]}->{r[1]}")
    await check_update.finish(message)


update_all = plugin.on_command("更新所有插件", "更新所有的插件")


@update_all.handle()
async def _():
    try:
        plugin_list = await PluginManager.refresh_list()
    except PluginError as e:
        await update_all.finish(f"获取插件信息失败：{e.prompt}")
    except Exception as e:
        await update_all.finish(f"获取插件信息失败：{type(e).__name__}: {e}")
    message = MessageBuilder().text("更新情况(更新完成后请重启):")
    for plugin_name in ServiceTools.service_list:
        if plugin_name in plugin_list:
            try:
                ok, msg = await PluginManager.update_plugin(plugin_name)
                if ok:
                    message.text(msg)
                else:
                    if msg.startswith("更新插件失败："):
                        message.text(msg)
            except Exception as e:
                message.text(str(e))
    await update_all.finish(message)


reload = plugin.on_command("重载插件", "重新加载指定插件")


@reload.handle()
async def _(args: Message = CommandArg()):
    plugin_name = args.extract_plain_text().replace(" ", "")
    if not plugin_name:
        await reload.finish("请输入插件名")
    try:
        await PluginRuntimeManager.reload_plugin(plugin_name)
        await reload.finish(f"已重载插件{plugin_name}")
    except FinishedException:
        pass
    except PluginError as e:
        await reload.finish(e.prompt)
    except Exception as e:
        await reload.finish(f"重载{plugin_name}发生错误:{e}")
