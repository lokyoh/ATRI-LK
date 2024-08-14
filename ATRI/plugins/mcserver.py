import re
from random import choice
from mcstatus import JavaServer

from nonebot.adapters.onebot.v11.helpers import Cooldown

from ATRI.service import Service

plugin = Service("MC服务器").document("查看MC服务器状态").type(Service.ServiceType.FUNCTION)

_lmt_notice = ["慢...慢一..点❤", "冷静1下", "歇会歇会~~", "呜呜...别急", "太快了...受不了", "不要这么快呀"]

mc = plugin.on_command(cmd="/mc", docs="查看mc.lokyoh.com服务器状态")


@mc.handle([Cooldown(30, prompt=choice(_lmt_notice))])
async def _():
    msg = await check_mc_status("mc.lokyoh.com", "查看世界幻想服务器状态")
    await mc.finish(msg)


mcserver = plugin.on_command(cmd="/mcserver", docs="查看mcserver.lokyoh.com服务器状态")


@mcserver.handle([Cooldown(30, prompt=choice(_lmt_notice))])
async def _():
    msg = await check_mc_status("mcserver.lokyoh.com", "我的世界服务器")
    await mcserver.finish(msg)


async def check_mc_status(name: str, sname: str):
    try:
        js = await JavaServer.async_lookup(name, timeout=5)
        status = js.status()
        if status.description.strip():
            description = re.sub(r'§.', '', status.description)
        online = f"{status.players.online}/{status.players.max}"
        player_list = []
        if status.players.online:
            if status.players.sample:
                player_list = [
                    p.name
                    for p in status.players.sample
                    if p.id != "00000000-0000-0000-0000-000000000000"
                ]
            if player_list:
                player_list = ", ".join(player_list)
            else:
                player_list = "没返回玩家列表"
        else:
            player_list = "没人在线"
        msg = f"{sname}\n{name}\n{description}\n在线：{online}\n◤ {player_list} ◢"
    except Exception as e:
        msg = f"名称：{sname} 查询失败！\n错误：{repr(e)}"
    return msg
