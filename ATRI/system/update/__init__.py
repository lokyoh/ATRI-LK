from ATRI.service import Service
from ATRI.permission import MASTER

from .data_source import Updater

plugin = Service("更新").document("ATRI-LK的更新服务").type(Service.ServiceType.SYSTEM).version("0.1.2")

check_update = plugin.on_command(cmd="检查更新", docs="检查最新版本", permission=MASTER)


@check_update.handle()
async def _():
    await check_update.finish(await Updater.check())


update = plugin.on_command(cmd="/更新", docs="目前处于测试阶段且只适用于clone项目", permission=MASTER)


@update.handle()
async def _():
    await check_update.finish(await Updater.update())
