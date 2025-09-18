import os
import time

from ATRI import TEMP_DIR
from ATRI.utils.event import BaseEvents, BaseEvent
from ATRI.log import log

daily_update_event = BaseEvents("daily_update")
"""每日数据更新事件"""


def daily_update():
    log.info("开始每日更新")
    daily_update_event.notify(BaseEvent("每日数据更新"))
    log.success("每日更新完成")


@daily_update_event.handle()
def clean_temp_files():
    now = time.time()
    cutoff = now - 24 * 3600
    deleted = 0
    for root, _, files in os.walk(TEMP_DIR):
        for file in files:
            file_path = os.path.join(root, file)
            try:
                file_time = os.path.getmtime(file_path)
                if file_time < cutoff:
                    os.remove(file_path)
                    deleted += 1
            except Exception as e:
                log.warning(f"跳过: {file_path}，错误: {e}")
    log.info(f"共删除 {deleted} 个临时文件。")
