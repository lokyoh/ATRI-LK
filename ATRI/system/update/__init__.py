from ATRI.service import Service

from .data_source import Updater

plugin = Service("更新").document("检查更新").type(Service.ServiceType.ADMIN).only_admin(True)

check_update = plugin.on_command(cmd="检查更新", docs="检查最新版本")


@check_update.handle()
async def _():
    await check_update.finish(await Updater.check())
