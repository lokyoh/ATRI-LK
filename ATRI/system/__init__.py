from ATRI import driver as atri_driver
from ATRI import __version__, __sub_version__
from ATRI.log import log
from ATRI.scheduler import scheduler
from ATRI.utils.check_update import CheckUpdate, get_version_num
from ATRI.database import init_database, close_database_connection

from time import sleep

driver = atri_driver()


@driver.on_startup
async def startup():
    await init_database()
    log.info(f"当前版本: {__version__} {__sub_version__}")
    log.info("开始检查更新...")
    commit_info = await CheckUpdate.show_latest_commit_info()
    if commit_info:
        log.info(f"Latest commit {commit_info[0]} | sha: {commit_info[1]} | time: {commit_info[2]}")
    l_v, l_v_t = await CheckUpdate.show_latest_version()
    if l_v and l_v_t:
        if (
                l_v[:11] > __version__ or (
                l_v[:11] == __version__ and l_v != f"{__version__} {__sub_version__}" and (
                __sub_version__[:3] == "Pre" or get_version_num(l_v[12:]) > get_version_num(__sub_version__)))
        ):
            log.warning("新版本已发布, 请更新")
            log.warning(f"最新版本: {l_v} 更新时间: {l_v_t}")
            sleep(3)
    if not scheduler.running:
        scheduler.start()
        log.info("定时任务已启用")
    log.success("アトリは、高性能ですから！")


@driver.on_shutdown
async def shutdown():
    await close_database_connection()
    scheduler.shutdown(False)
    log.info("感谢使用")
