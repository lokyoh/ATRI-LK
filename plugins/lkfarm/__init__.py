import re

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.params import CommandArg

from ATRI.service import Service
from ATRI.message import img_msg
from ATRI.system.lkbot.checker import is_lk_user
from ATRI.system.lkbot.util import lk_util

from .data_source import farm_system

plugin = Service("lk农场").document("l_o_o_k的农场插件").type(Service.ServiceType.LKPLUGIN).main_cmd("/farm").version(
    "0.1.0")

my_farm = plugin.on_command("我的农场", "查看自己的农场")


@my_farm.handle()
async def _(event: GroupMessageEvent):
    await farm_system.check_user(my_farm, event)
    await my_farm.finish(img_msg(await farm_system.farm_info(event.user_id)))


seeding = plugin.on_command("/播种", "在田上播种\n使用方法:/播种 要选择的所有位置 种子名称")


@seeding.handle()
async def _(event: GroupMessageEvent, arg: Message = CommandArg()):
    await farm_system.check_user(seeding, event)
    text = arg.extract_plain_text().upper()
    match = re.match(r"((?: ?[A-D][1-8]-[A-D][1-8]| ?[A-D][1-8])+) (.*)$", text)
    if not match:
        await seeding.finish("请检查输入:\n1.位置是否正确\n2.A1-B1需要连在一起\n3.是否含有种子名")
    crop = match[2]
    location = match[1].replace(f" {crop}", "")
    p_list = farm_system.get_positions(location)
    if len(p_list) > 0:
        resp = "开始操作:"
        for p in p_list:
            r = farm_system.seeding(event.user_id, p, crop)
            if r:
                resp += f"\n{r}"
        if resp == "开始操作:":
            resp += f"\n种植 {crop} 成功"
        await seeding.finish(resp)
    await seeding.finish("未识别出有效位置")


hoeing = plugin.on_command("/锄地", "为田锄地\n使用方法:/锄地 要选择的所有位置")


@hoeing.handle()
async def _(event: GroupMessageEvent, arg: Message = CommandArg()):
    await farm_system.check_user(hoeing, event)
    location = arg.extract_plain_text().upper()
    p_list = farm_system.get_positions(location)
    if len(p_list) > 0:
        resp = "开始操作:"
        for p in p_list:
            r = farm_system.hoeing(event.user_id, p)
            if r:
                resp += f"\n{r}"
        if resp == "开始操作:":
            resp += f"\n锄地成功"
        await hoeing.finish(resp)
    await hoeing.finish("未识别出有效位置")


watering = plugin.on_command("/浇水", "为田浇水\n使用方法:/浇水 要选择的所有位置")


@watering.handle()
async def _(event: GroupMessageEvent, arg: Message = CommandArg()):
    await farm_system.check_user(watering, event)
    location = arg.extract_plain_text().upper()
    p_list = farm_system.get_positions(location)
    if len(p_list) > 0:
        resp = "开始操作:"
        for p in p_list:
            r = farm_system.watering(event.user_id, p)
            if r:
                resp += f"\n{r}"
        if resp == "开始操作:":
            resp += f"\n浇水成功"
        await watering.finish(resp)
    await watering.finish("未识别出有效位置")


harvesting = plugin.on_command("/收获", "收获作物\n使用方法:/收获 要选择的所有位置")


@harvesting.handle()
async def _(event: GroupMessageEvent, arg: Message = CommandArg()):
    await farm_system.check_user(harvesting, event)
    location = arg.extract_plain_text().upper()
    p_list = farm_system.get_positions(location)
    if len(p_list) > 0:
        resp = "开始操作:"
        for p in p_list:
            r = farm_system.harvesting(event.user_id, p)
            if r:
                resp += f"\n{r}"
        if resp == "开始操作:":
            resp += f"\n收获成功"
        await harvesting.finish(resp)
    await harvesting.finish("未识别出有效位置")


new_farm = plugin.cmd_as_group("新农场", "创建一个新农场")


@new_farm.handle()
async def _(event: GroupMessageEvent):
    await is_lk_user(new_farm, event)
    user_id = event.user_id
    user_name = lk_util.get_name(user_id)
    if farm_system.new_farm(user_id):
        await new_farm.finish(f"{user_name}的农场创建成功!")
    await new_farm.finish(f"{user_name}你已经创建过一个农场了")
