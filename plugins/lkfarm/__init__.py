import re

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.params import CommandArg

from ATRI.bot import BotUtils
from ATRI.message import img_msg
from ATRI.permission import ADMIN
from ATRI.service import Service
from ATRI.system.lkapi.entity.user import get_user_data

from .config import LKFarmConfig

plugin = Service(
    "农场", "ATRI的农场插件", "0.4.0", Service.ServiceType.ENTERTAINMENT
).main_cmd("农场")
config = plugin.add_plugin_config(LKFarmConfig)

from .data_source import CheckFarmUser, farm_system  # noqa: E402
from .system.farm_user import get_user_farm_data  # noqa: E402

cmd_str = BotUtils.get_command_start()

my_farm = plugin.on_command("我的农场", "查看自己的农场")


@my_farm.handle([CheckFarmUser])
async def _(event: GroupMessageEvent):
    await my_farm.finish(img_msg(await farm_system.farm_info(event.user_id)))


seeding = plugin.on_command(
    "播种",
    f"在田上播种\n使用方法:{cmd_str}播种 要选择的所有位置 种子名称",
    aliases={"种植"},
)


@seeding.handle([CheckFarmUser])
async def _(event: GroupMessageEvent, arg: Message = CommandArg()):
    text = arg.extract_plain_text().upper()
    match = re.match(r"((?: ?[A-D][1-8][-_][A-D][1-8]| ?[A-D][1-8])+) (.*)$", text)
    if not match:
        await seeding.finish(
            "请检查输入:\n1.位置是否正确\n2.A1-B1需要连在一起\n3.是否含有种子名"
        )
    crop = match[2]
    location = match[1].replace(f" {crop}", "")
    p_list = farm_system.get_positions(location)
    if len(p_list) > 0:
        resp = "开始操作:"
        r_l = {}
        user_id = event.get_user_id()
        with get_user_data(user_id) as user_data:
            with get_user_farm_data(user_id) as f_user_data:
                for p in p_list:
                    r = farm_system.seeding(f_user_data, p, crop, user_data)
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
                    resp += f"{p[0]}{p[1]} "
                resp += m
        await seeding.finish(resp)
    await seeding.finish("未识别出有效位置")


hoeing = plugin.on_command(
    "锄地", f"为田锄地\n使用方法:{cmd_str}锄地 要选择的所有位置", aliases={"耕地"}
)


@hoeing.handle([CheckFarmUser])
async def _(event: GroupMessageEvent, arg: Message = CommandArg()):
    location = arg.extract_plain_text().upper()
    p_list = farm_system.get_positions(location)
    if len(p_list) > 0:
        resp = "开始操作:"
        r_l = {}
        with get_user_farm_data(event.user_id) as f_user_data:
            for p in p_list:
                r = farm_system.hoeing(f_user_data, p)
                if r:
                    if r not in r_l.keys():
                        r_l[r] = []
                    r_l[r].append(p)
        if r_l == {}:
            resp += "\n锄地成功"
        else:
            for m in r_l.keys():
                resp += "\n"
                for p in r_l[m]:
                    resp += f"{p[0]}{p[1]} "
                resp += m
        await hoeing.finish(resp)
    await hoeing.finish("未识别出有效位置")


watering = plugin.on_command(
    "浇水", f"为田浇水\n使用方法:{cmd_str}浇水 要选择的所有位置"
)


@watering.handle([CheckFarmUser])
async def _(event: GroupMessageEvent, arg: Message = CommandArg()):
    location = arg.extract_plain_text().upper()
    p_list = farm_system.get_positions(location)
    if len(p_list) > 0:
        resp = "开始操作:"
        r_l = {}
        with get_user_farm_data(event.user_id) as f_user_data:
            for p in p_list:
                r = farm_system.watering(f_user_data, p)
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
                    resp += f"{p[0]}{p[1]} "
                resp += m
        await watering.finish(resp)
    await watering.finish("未识别出有效位置")


harvesting = plugin.on_command(
    "收获", f"收获作物\n使用方法:{cmd_str}收获 要选择的所有位置"
)


@harvesting.handle([CheckFarmUser])
async def _(event: GroupMessageEvent, arg: Message = CommandArg()):
    location = arg.extract_plain_text().upper()
    p_list = farm_system.get_positions(location)
    if len(p_list) > 0:
        resp = "开始操作:"
        r_l = {}
        user_id = event.get_user_id()
        with get_user_data(user_id) as user_data:
            with get_user_farm_data(user_id) as f_user_data:
                for p in p_list:
                    r = farm_system.harvesting(f_user_data, p, user_data)
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
                    resp += f"{p[0]}{p[1]} "
                resp += m
        await harvesting.finish(resp)
    await harvesting.finish("未识别出有效位置")


fertilization = plugin.on_command(
    "施肥", f"为作物施肥\n使用方法:{cmd_str}施肥 要选择的所有位置 肥料名称"
)


@fertilization.handle([CheckFarmUser])
async def _(event: GroupMessageEvent, arg: Message = CommandArg()):
    text = arg.extract_plain_text().upper()
    match = re.match(r"((?: ?[A-D][1-8][-_][A-D][1-8]| ?[A-D][1-8])+) (.*)$", text)
    if not match:
        await fertilization.finish(
            "请检查输入:\n1.位置是否正确\n2.A1-B1需要连在一起\n3.是否含有肥料名称"
        )
    fertilizer = match[2]
    location = match[1].replace(f" {fertilizer}", "")
    p_list = farm_system.get_positions(location)
    if len(p_list) > 0:
        resp = "开始操作:"
        r_l = {}
        user_id = event.get_user_id()
        with get_user_data(user_id) as user_data:
            with get_user_farm_data(user_id) as f_user_data:
                for p in p_list:
                    r = farm_system.fertilization(f_user_data, p, fertilizer, user_data)
                    if r:
                        if r not in r_l.keys():
                            r_l[r] = []
                        r_l[r].append(p)
        if r_l == {}:
            resp += "\n施肥成功"
        else:
            for m in r_l.keys():
                resp += "\n"
                for p in r_l[m]:
                    resp += f"{p[0]}{p[1]} "
                resp += m
        await fertilization.finish(resp)
    await fertilization.finish("未识别出有效位置")


c_remove = plugin.cmd_as_group(
    "铲除", f"移除田地上的作物\n使用方法:{cmd_str}农场.铲除 要选择的所有位置"
)


@c_remove.handle([CheckFarmUser])
async def _(event: GroupMessageEvent, arg: Message = CommandArg()):
    location = arg.extract_plain_text().upper()
    p_list = farm_system.get_positions(location)
    if len(p_list) > 0:
        resp = "开始操作:"
        r_l = {}
        with get_user_farm_data(event.user_id) as f_user_data:
            for p in p_list:
                r = farm_system.c_remove(f_user_data, p)
                if r:
                    if r not in r_l.keys():
                        r_l[r] = []
                    r_l[r].append(p)
        if r_l == {}:
            resp += "\n铲除成功"
        else:
            for m in r_l.keys():
                resp += "\n"
                for p in r_l[m]:
                    resp += f"{p[0]}{p[1]} "
                resp += m
        await c_remove.finish(resp)
    await c_remove.finish("未识别出有效位置")


weather_forecast = plugin.cmd_as_group(
    "天气预报订阅", "订阅或关闭本群的天气预报的订阅", permission=ADMIN
)


@weather_forecast.handle()
async def _(event: GroupMessageEvent):
    group_id = event.group_id
    conf = config.config()
    if group_id in conf.weather_forecast_group:
        conf.weather_forecast_group.remove(group_id)
        config.change_config(conf)
        msg = "已取消了本群的农场天气预报"
    else:
        conf.weather_forecast_group.append(group_id)
        config.change_config(conf)
        msg = "已为本群订阅了农场天气预报"
    await weather_forecast.send(msg)


easy_operation = plugin.cmd_as_group("一键操作", "为田锄地、浇水与收获")


@easy_operation.handle()
async def _(event: GroupMessageEvent):
    user_id = event.get_user_id()
    with get_user_data(user_id) as user_data:
        with get_user_farm_data(user_id) as f_user_data:
            farm_system.easy_operation(f_user_data, user_data)
    await easy_operation.finish("一键操作完成", at_sender=True)
