from nonebot.adapters.onebot.v11 import MessageEvent, ActionFailed
from nonebot.internal.params import ArgPlainText

from ATRI.service import Service
from ATRI.permission import MASTER

from .config import HelpConfig

plugin = Service("帮助").document("ATRI 的食用指南~").type(Service.ServiceType.SYSTEM).version("2.0.0")
help_config: HelpConfig = plugin.add_plugin_config(HelpConfig).config()

from .data_source import Helper, help_type

plugin.on_startup(lambda: Helper().init_services())

menu = plugin.on_command("/菜单", "获取食用bot的方法", aliases={"/menu"})


@menu.handle()
async def _():
    await menu.finish(Helper().menu())


about = plugin.on_command("/关于", "获取关于bot的信息", aliases={"/about"})


@about.handle()
async def _():
    await about.finish(Helper().about())


service_list = plugin.on_command("/服务列表", "获取服务列表", aliases={"/功能列表"})


@service_list.handle()
async def _(event: MessageEvent):
    try:
        await service_list.finish(await Helper().get_service_list(event))
    except ActionFailed:
        await service_list.finish(Helper().get_text_list())


service_info = plugin.on_command("/帮助", "获取对应服务详细信息", aliases={"/help"})


@service_info.handle()
async def _ready_service_info(event: MessageEvent):
    msg = str(event.get_message()).split(" ")
    try:
        service = msg[1]
    except Exception:
        service = "master"
    try:
        cmd = msg[2]
    except Exception:
        cmd = None
    if not cmd:
        repo = Helper().service_info(service)
        await service_info.finish(repo)
    repo = Helper().cmd_info(service, cmd)
    await service_info.finish(repo)


change_type = plugin.on_command('/帮助切换形式', '切换帮助的形式', permission=MASTER)


@change_type.got("help_type",
                 f"请输入要选择的类型名:\n{'\n'.join(f'{i}.{_type}' for i, _type in enumerate(help_type, 1))}")
async def _(arg: str = ArgPlainText('help_type')):
    if arg in help_type:
        help_config.help_type = arg
        plugin.plugin_config().change_config(help_config)
    else:
        await change_type.finish("请输入正确的类型")
    await change_type.finish("切换成功")
