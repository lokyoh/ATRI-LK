from ATRI.event import Priority, daily_update
from ATRI.log import log

IMAGE_CLEANUP_TIME = 30 * 24 * 60 * 60


@daily_update(priority=Priority.LOW)
async def agent_daily_task():
    from .memory.manage import summarize_memories
    from .schedule import generate_schedule

    await summarize_memories()
    await generate_schedule()


@daily_update(priority=Priority.NORMAL)
async def agent_cleanup_task():
    log.info("执行 Agent 清理任务")
    from datetime import date, timedelta

    from ATRI.utils.datetime import now, now_timestamp, today
    from ATRI.utils.sqlite import DataBase

    from ..service import plugin

    cutoff = now_timestamp() - IMAGE_CLEANUP_TIME
    deleted_rows = (
        DataBase("atri.db").get_exist_table("IMAGE").delete(f"UPDATE_AT < {cutoff}")
    )
    if deleted_rows > 0:
        log.info(f"清理了 {deleted_rows} 个图片缓存记录")

    path = plugin.get_path() / "chat"
    history_cutoff = today() - timedelta(days=30)
    removed_files = 0
    for file_path in path.rglob("*"):
        if not file_path.is_file():
            continue
        try:
            file_date = date.fromisoformat(file_path.stem)
        except ValueError:
            continue
        if file_date < history_cutoff:
            file_path.unlink()
            removed_files += 1
    if removed_files > 0:
        log.info(f"清理了 {removed_files} 个聊天历史文件")

    current_time = now()
    target_year = current_time.year - 2
    start_of_year = current_time.replace(
        year=target_year, month=1, day=1, hour=0, minute=0, second=0, microsecond=0
    )
    start_of_next_year = start_of_year.replace(year=target_year + 1)
    usage_db = DataBase("agent.db")
    try:
        deleted_usage_rows = usage_db.get_exist_table("tokens").delete(
            "created_at >= "
            f"{start_of_year.timestamp()} AND created_at < {start_of_next_year.timestamp()}"
        )
    finally:
        usage_db.disconnect()
    if deleted_usage_rows > 0:
        log.info(f"已删除 {target_year} 年模型 Token 使用记录：{deleted_usage_rows} 条")


def init():
    pass
