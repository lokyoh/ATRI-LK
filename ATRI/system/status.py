import os
import time
from datetime import UTC, datetime
from typing import Tuple

import psutil
from nonebot import get_bot
from nonebot.adapters.onebot.v11 import MessageSegment

from ATRI.exceptions import BaseBotException
from ATRI.log import log
from ATRI.service import Service
from ATRI.system.lkbot.tools.get_pic import local_image
from ATRI.utils import Limiter
from ATRI.utils.img_editor import IMGEditor
from ATRI.utils.model import BaseModel

plugin = Service("状态", "检查 ATRI 状态", "1.1.1", Service.ServiceType.SYSTEM)


class GetStatusError(BaseBotException):
    prompt = "获取状态失败"


class StatusConfig(BaseModel):
    check_status: bool = True
    hours: int = 1
    minutes: int = 0


config: StatusConfig = plugin.add_plugin_config(StatusConfig).config()

ping = plugin.on_command("ping", "检测 ATRI 是否存活")


@ping.handle()
async def _():
    await ping.finish("I'm fine.")


status = plugin.on_command("status", "检查 ATRI 运行资源占用")


@status.handle()
async def _():
    msg, _ = get_status()
    await status.send(msg)


check = plugin.on_command("status.check", "定时检查开关")


@check.handle()
async def _():
    jobs = plugin.scheduler_jobs()
    if jobs.has_job("状态检查"):
        jobs.remove_job("状态检查")
        await check.finish("定时检查已关闭")
    else:
        add_check_job()
        await check.finish("定时检查已开启")


limiter = Limiter(5, 21600)


async def check_status():
    log.info("检查资源消耗中...")
    msg, stat = get_status()
    if not stat:
        log.warning("资源消耗异常")

        if limiter.get_times("114514") > 5:
            return

        try:
            bot = get_bot()
        except Exception:
            bot = None
        if not limiter.check("114514"):
            msg = "状态检查提示已达限制, 将冷却 6h"

        try:
            if bot:
                await plugin.send_to_master(msg)
            limiter.increase("114514")
        except Exception:
            return
    else:
        log.info("资源消耗正常")


def add_check_job():
    plugin.scheduler_jobs().add_job(
        check_status,
        "状态检查",
        trigger="interval",
        hours=config.hours,
        minutes=config.minutes,
        misfire_grace_time=15,
    )


if config.check_status:
    add_check_job()


def get_status() -> Tuple[MessageSegment, bool]:
    try:
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory().percent
        disk = psutil.disk_usage("/").percent
        inte_send = psutil.net_io_counters().bytes_sent / (1024 * 1024)
        inte_recv = psutil.net_io_counters().bytes_recv / (1024 * 1024)

        process = psutil.Process(os.getpid())
        b_cpu = process.cpu_percent(interval=1)
        b_mem = process.memory_percent(memtype="rss")

        now = time.time()
        boot = psutil.boot_time()
        b = process.create_time()
        boot_time = str(
            datetime.fromtimestamp(now, UTC).replace(microsecond=0)
            - datetime.fromtimestamp(boot, UTC).replace(microsecond=0)
        )
        bot_time = str(
            datetime.fromtimestamp(now, UTC).replace(microsecond=0)
            - datetime.fromtimestamp(b, UTC).replace(microsecond=0)
        )
    except Exception:
        raise GetStatusError("Failed to get status.")

    msg = "アトリは、高性能ですから！"
    if cpu > 90:
        msg = "咱感觉有些头晕..."
        is_ok = False
        if mem > 90:
            msg = "咱感觉有点头晕并且有点累..."
            is_ok = False
    elif mem > 90:
        msg = "咱感觉有点累..."
        is_ok = False
    elif disk > 90:
        msg = "咱感觉身体要被塞满了..."
        is_ok = False
    else:
        is_ok = True

    # 720 * 1280
    b_mem = "%.1f%%" % b_mem
    inte_send = f"{round(inte_send / 1024, 2)}GB"
    inte_recv = f"{round(inte_recv / 1024, 2)}GB"
    img = (
        IMGEditor(local_image())
        .add_rectangle(10, 10, 700, 1260, 192, 20)
        .add_middle_text(360, 50, "状态预览", 100)
        .add_text(30, 220, f"CPU占用: {b_cpu}% 总 {cpu}%", 50)
        .add_text(30, 310, f"运行内存: {b_mem} 总 {mem}%", 50)
        .add_text(30, 400, f"硬盘使用: {disk}%", 50)
        .add_text(30, 490, f"网络发送: {inte_send}", 50)
        .add_text(30, 580, f"网络接收: {inte_recv}", 50)
        .add_middle_text(360, 720, "运行时间", 100)
        .add_text(30, 900, f"bot: {bot_time}", 50)
        .add_text(30, 990, f"系统: {boot_time}", 50)
        .add_middle_text(360, 1160, msg, 40)
    )

    return MessageSegment.image(img.to_bytes()), is_ok
