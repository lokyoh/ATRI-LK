import asyncio

from ATRI import __sub_version__, __version__
from ATRI import driver as atri_driver
from ATRI.database import close_database_connection, init_database
from ATRI.log import log
from ATRI.scheduler import scheduler
from ATRI.utils.check_update import CheckUpdate, is_newer_version

driver = atri_driver()


@driver.on_startup
async def startup():
    await init_database()
    log.info(f"当前版本: {__version__} {__sub_version__}")
    log.info("开始检查更新...")
    latest_info = await CheckUpdate.get_latest_info()
    if latest_info and is_newer_version(
        latest_info.version, __version__, __sub_version__
    ):
        log.warning("新版本已发布, 请更新")
        log.warning(
            f"最新版本: {latest_info.version} 更新时间: {latest_info.update_time}\n更新信息:{latest_info.info}"
        )
        await asyncio.sleep(3)
    else:
        log.info("当前已是最新版本")
    if not scheduler.running:
        scheduler.start()
        log.info("定时任务已启用")
    log.success("アトリは、高性能ですから！")


@driver.on_shutdown
async def shutdown():
    await close_database_connection()
    scheduler.shutdown(False)
    log.info("感谢使用")
