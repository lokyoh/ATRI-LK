import os
from pathlib import Path
import sys

from nonebot.adapters.onebot.v11 import Bot, Event, PrivateMessageEvent, GroupMessageEvent

from ATRI import driver as atri_driver
from ATRI.service import Service
from ATRI.permission import MASTER

plugin = Service(
    service="重启",
    docs="重新启动ATRI",
    type=Service.ServiceType.SYSTEM,
    version="0.2.0"
)

PLUGIN_DIR = Path(".") / "data" / "plugins" / "restart"
RESTART_TEMP = PLUGIN_DIR / "restart_temp"
PLUGIN_DIR.mkdir(parents=True, exist_ok=True)

driver = atri_driver()


async def restart_bot(bot_id: str, target_id):
    with open(RESTART_TEMP, "w", encoding="utf8") as f:
        f.write(f"{bot_id} {target_id}")
    os.execv(sys.executable, [sys.executable] + sys.argv)


restart = plugin.on_command("/重启", "重新启动ATRI", permission=MASTER)


@restart.handle()
async def _(bot: Bot, event: Event):
    await restart.send("ATRI正在重新启动...")
    if type(event) is PrivateMessageEvent:
        await restart_bot(bot.self_id, f"p{event.get_user_id()}")
    elif type(event) is GroupMessageEvent:
        event: GroupMessageEvent
        await restart_bot(bot.self_id, f"g{event.group_id}")
    else:
        await restart.finish("暂不支持")


@driver.on_bot_connect
async def _(bot: Bot):
    if RESTART_TEMP.exists():
        with open(RESTART_TEMP, "r", encoding="utf8") as f:
            bot_id, target_id = f.read().split()
        if bot.self_id == bot_id:
            target = target_id[1:]
            message = "ATRI重启成功"
            if target_id[0] == "p":
                await bot.send_private_msg(user_id=int(target), message=message)
            else:
                await bot.send_group_msg(group_id=int(target), message=message)
            RESTART_TEMP.unlink()
