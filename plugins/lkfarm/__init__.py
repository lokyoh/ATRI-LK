import re

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.params import CommandArg

from ATRI.service import Service
from ATRI.message import img_msg
from ATRI.system.lkapi.bot import util as lk_util
from ATRI.system.lkapi.bot.checker import IsLkUser

from .data_source import farm_system, CheckFarmUser

plugin = Service(
    "农场",
    "ATRI的农场插件",
    "0.1.5",
    Service.ServiceType.LKPLUGIN
).main_cmd("/farm")

my_farm = plugin.on_command("我的农场", "查看自己的农场")


@my_farm.handle([CheckFarmUser])
async def _(event: GroupMessageEvent):
    await my_farm.finish(img_msg(await farm_system.farm_info(event.user_id)))


seeding = plugin.on_command("/播种", "在田上播种\n使用方法:/播种 要选择的所有位置 种子名称")


@seeding.handle([CheckFarmUser])
async def _(event: GroupMessageEvent, arg: Message = CommandArg()):
    text = arg.extract_plain_text().upper()
    match = re.match(r"((?: ?[A-D][1-8][-_][A-D][1-8]| ?[A-D][1-8])+) (.*)$", text)
    if not match:
        await seeding.finish("请检查输入:\n1.位置是否正确\n2.A1-B1需要连在一起\n3.是否含有种子名")
    crop = match[2]
    location = match[1].replace(f" {crop}", "")
    p_list = farm_system.get_positions(location)
    if len(p_list) > 0:
        resp = "开始操作:"
        r_l = {}
        for p in p_list:
            r = farm_system.seeding(event.user_id, p, crop)
            if r:
                if r not in r_l.keys():
                    r_l[r] = []
                r_l[r].append(p)
        if r_l == {}:
            resp += "\n种植成功"
        else:
            for m in r_l.keys():
                resp += "\n"
                for p in r_l[m]:
                    resp += f'{p[0]}{p[1]} '
                resp += m
        await seeding.finish(resp)
    await seeding.finish("未识别出有效位置")


hoeing = plugin.on_command("/锄地", "为田锄地\n使用方法:/锄地 要选择的所有位置")


@hoeing.handle([CheckFarmUser])
async def _(event: GroupMessageEvent, arg: Message = CommandArg()):
    location = arg.extract_plain_text().upper()
    p_list = farm_system.get_positions(location)
    if len(p_list) > 0:
        resp = "开始操作:"
        r_l = {}
        for p in p_list:
            r = farm_system.hoeing(event.user_id, p)
            if r:
                if r not in r_l.keys():
                    r_l[r] = []
                r_l[r].append(p)
        if r_l == {}:
            resp += "\n耕种成功"
        else:
            for m in r_l.keys():
                resp += "\n"
                for p in r_l[m]:
                    resp += f'{p[0]}{p[1]} '
                resp += m
        await hoeing.finish(resp)
    await hoeing.finish("未识别出有效位置")


watering = plugin.on_command("/浇水", "为田浇水\n使用方法:/浇水 要选择的所有位置")


@watering.handle([CheckFarmUser])
async def _(event: GroupMessageEvent, arg: Message = CommandArg()):
    location = arg.extract_plain_text().upper()
    p_list = farm_system.get_positions(location)
    if len(p_list) > 0:
        resp = "开始操作:"
        r_l = {}
        for p in p_list:
            r = farm_system.watering(event.user_id, p)
            if r:
                if r not in r_l.keys():
                    r_l[r] = []
                r_l[r].append(p)
        if r_l == {}:
            resp += "\n浇水成功"
        else:
            for m in r_l.keys():
                resp += "\n"
                for p in r_l[m]:
                    resp += f'{p[0]}{p[1]} '
                resp += m
        await watering.finish(resp)
    await watering.finish("未识别出有效位置")


harvesting = plugin.on_command("/收获", "收获作物\n使用方法:/收获 要选择的所有位置")


@harvesting.handle([CheckFarmUser])
async def _(event: GroupMessageEvent, arg: Message = CommandArg()):
    location = arg.extract_plain_text().upper()
    p_list = farm_system.get_positions(location)
    if len(p_list) > 0:
        resp = "开始操作:"
        r_l = {}
        for p in p_list:
            r = farm_system.harvesting(event.user_id, p)
            if r:
                if r not in r_l.keys():
                    r_l[r] = []
                r_l[r].append(p)
        if r_l == {}:
            resp += "\n收获成功"
        else:
            for m in r_l.keys():
                resp += "\n"
                for p in r_l[m]:
                    resp += f'{p[0]}{p[1]} '
                resp += m
        await harvesting.finish(resp)
    await harvesting.finish("未识别出有效位置")


new_farm = plugin.cmd_as_group("新农场", "创建一个新农场")


@new_farm.handle([IsLkUser])
async def _(event: GroupMessageEvent):
    user_id = event.user_id
    user_name = lk_util.get_name(user_id)
    if farm_system.new_farm(user_id):
        await new_farm.finish(f"{user_name}的农场创建成功!")
    await new_farm.finish(f"{user_name}你已经创建过一个农场了")
