from ATRI.service import Service

from .data_source import Updater

plugin = Service("更新").document("ATRI-LK的更新服务v0.1.1").type(Service.ServiceType.ADMIN).only_admin(True)

check_update = plugin.on_command(cmd="检查更新", docs="检查最新版本")


@check_update.handle()
async def _():
    await check_update.finish(await Updater.check())


update = plugin.on_command(cmd="/测试更新", docs="目前处于测试阶段")


@update.handle()
async def _():
    await check_update.finish(await Updater.update())
