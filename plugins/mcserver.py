import re
from random import choice
from mcstatus import JavaServer

from nonebot.adapters.onebot.v11 import Message
from nonebot.adapters.onebot.v11.helpers import Cooldown
from nonebot.internal.params import ArgPlainText
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from ATRI.service import Service

plugin = Service("MC服务器").document("查看MC服务器状态").type(Service.ServiceType.FUNCTION)

_lmt_notice = ["慢...慢一..点❤", "冷静1下", "歇会歇会~~", "呜呜...别急", "太快了...受不了", "不要这么快呀"]

mc = plugin.on_command(cmd="/mc", docs="查看MINECRAFT服务器状态")

s_names = {"mc.lokyoh.com": "世界幻想服务器"}


@mc.handle([Cooldown(30, prompt=choice(_lmt_notice))])
async def _(matcher: Matcher, args: Message = CommandArg()):
    if args.extract_plain_text():
        matcher.set_arg("server_name", args)


@mc.got("server_name", "要查询那个服务器呢")
async def _(s_name=ArgPlainText("server_name")):
    s_name = s_name.replace(" ", "")
    msg = await check_mc_status(s_name, s_names[s_name] if s_name in s_names else "我的世界服务器")
    await mc.finish(msg)


async def check_mc_status(s_name: str, name: str):
    try:
        js = await JavaServer.async_lookup(name, timeout=5)
        status = js.status()
        description = "获取介绍失败"
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
        msg = f"{s_name}\n{name}\n{description}\n在线：{online}\n◤ {player_list} ◢"
    except Exception as e:
        msg = f"名称：{s_name} 查询失败！\n错误：{repr(e)}"
    return msg
