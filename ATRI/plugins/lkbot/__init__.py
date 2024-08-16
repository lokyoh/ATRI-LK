from random import choice

from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11.event import GroupMessageEvent, Event
from nonebot.adapters.onebot.v11.helpers import Cooldown, CooldownIsolateLevel
from nonebot.adapters.onebot.v11.message import Message
from nonebot.matcher import Matcher
from nonebot.params import CommandArg, ArgPlainText

from ATRI.log import log
from ATRI.permission import ADMIN, MASTER
from ATRI.service import Service

from .checker import is_lk_user
from .config import config
from .data_source import LKBot
from .util import lk_util, PLUGIN_VERSION
from .system.data.item import items
from .system.data.shop import shops
from .system.data.user import users

plugin = Service("lk插件").document(f"l_o_o_k的综合性插件v{PLUGIN_VERSION}").type(
    Service.ServiceType.LKPLUGIN).main_cmd("/lk")

_lmt_notice = ["慢...慢一..点❤", "冷静1下", "歇会歇会~~", "呜呜...别急", "太快了...受不了", "不要这么快呀"]

sign_in = plugin.on_command(cmd='签到', docs="全新界面的签到系统")


@sign_in.handle([Cooldown(60, prompt=choice(_lmt_notice), isolate_level=CooldownIsolateLevel.GROUP_USER)])
async def _(event: Event):
    await is_lk_user(sign_in, event)
    r18_mode = not lk_util.is_safe_mode_group(event.group_id) if type(event) is GroupMessageEvent else True
    await sign_in.finish(await LKBot.sign_in(event.get_user_id(), r18_mode))


my_info = plugin.on_command(cmd="我的信息", docs="查询自己的信息")


@my_info.handle()
async def _(event: Event):
    await is_lk_user(my_info, event)
    await my_info.finish(LKBot.get_info(event.get_user_id()))


my_backpack = plugin.on_command(cmd="我的背包", docs="查看背包中的内容")


@my_backpack.handle()
async def _(event: Event):
    await is_lk_user(my_backpack, event)
    user_id = event.get_user_id()
    backpack: dict = users.get_backpack(user_id).bp_to_dict()
    resp = f'{lk_util.get_name(user_id)} 的背包:\n名称-类型-数量\n'
    num = len(backpack)
    i = 0
    j = 1
    for item in backpack.keys():
        if i == j * 20:
            await my_backpack.send(resp + f'页数:{j} 物品总数:{i}/{num}')
            resp = ''
            j += 1
        i += 1
        resp += f'{i}.{item}-{items.get_item_by_name(item).get_item_type()}-{backpack[item]["num"]}\n'
    await my_backpack.send(resp + f'页数:{j} 物品总数:{i}/{num}')


item_inquiry = plugin.on_command(cmd="物品查询", docs="查询指定物品信息")


@item_inquiry.handle()
async def _(matcher: Matcher, args: Message = CommandArg()):
    if args.extract_plain_text():
        matcher.set_arg("item_inquiry_name", args)


@item_inquiry.got("item_inquiry_name", prompt="要查询的物品呢？速速")
async def _(item_name=ArgPlainText("item_inquiry_name")):
    item_name = lk_util.clean_str(item_name)
    if items.has_item(item_name):
        item = items.get_item_by_name(item_name)
        item_info = f'''{item_name}:
{item.get_item_info()}
类型:{item.get_item_type()}
价值:{item.get_item_price_dis()}
可使用:{item.item_can_use()}'''
        await item_inquiry.finish(item_info)
    await item_inquiry.finish(f"找不到指定物品 {item_name}")


use_item = plugin.on_command(cmd="/使用", docs="使用指定物品,'全部物品'使用全部,'物品*n'使用n个物品")


@use_item.handle()
async def _(event: Event, matcher: Matcher, args: Message = CommandArg()):
    await is_lk_user(use_item, event)
    if args.extract_plain_text():
        matcher.set_arg("use_item_name", args)


@use_item.got("use_item_name", prompt="要使用的物品呢？速速")
async def _(event: Event, item_name=ArgPlainText("use_item_name")):
    item_name = lk_util.clean_str(item_name)
    item_name, num = lk_util.extract_number(item_name)
    if not items.has_item(item_name):
        await use_item.finish(f"找不到指定物品 {item_name}")
    if num <= 0 and num != -1:
        return use_item.finish("数量不符合规范")
    have_used, msg = lk_util.use_item(event.get_user_id(), item_name, num)
    if have_used:
        await use_item.finish(f"使用成功:\n{msg}")
    else:
        await use_item.finish(f"使用失败:\n{msg}")


recycle_item = plugin.on_command(cmd="/回收", docs="将指定数量物品换成ATRI币,'全部物品'回收全部,'物品*n'回收n个物品")


@recycle_item.handle()
async def _(event: Event, matcher: Matcher, args: Message = CommandArg()):
    await is_lk_user(my_backpack, event)
    if args.extract_plain_text():
        matcher.set_arg("recycle_item", args)


@recycle_item.got("recycle_item", prompt="要回收的物品呢？速速")
async def _(event: Event, item_name=ArgPlainText("recycle_item")):
    item_name = lk_util.clean_str(item_name)
    item_name, num = lk_util.extract_number(item_name)
    if not items.has_item(item_name):
        await recycle_item.finish(f"找不到指定物品 {item_name}")
    if items.get_item_by_name(item_name).get_item_price() == 0:
        await recycle_item.finish(f"物品 {item_name} 不可出售")
    if num <= 0 and num != -1:
        await recycle_item.finish("数量不符合规范")
    msg = lk_util.sell_item(event.get_user_id(), item_name, num)
    await recycle_item.finish(msg)


shop_list = plugin.on_command(cmd="商店列表", docs="列出所有商店")


@shop_list.handle()
async def _():
    shop_l = shops.get_shop_names()
    num = len(shop_l)
    resp = "商店列表:\n"
    i = 0
    j = 1
    for shop in shop_l:
        if i == j * 20:
            await my_backpack.send(resp + f'页数:{j} 商店总数:{i}/{num}')
            resp = ''
            j += 1
        i += 1
        resp += f'{i}.{shop}\n'
    await my_backpack.send(resp + f'页数:{j} 商店总数:{i}/{num}')


goods_list = plugin.on_command(cmd="商品列表", docs="列出指定商店的商品列表")


@goods_list.handle()
async def _(matcher: Matcher, args: Message = CommandArg()):
    if args.extract_plain_text():
        matcher.set_arg("shop_name", args)


@goods_list.got("shop_name", prompt="要浏览那个商店呢?速速")
async def _(shop_name=ArgPlainText("shop_name")):
    shop_name = lk_util.clean_str(shop_name)
    if shops.has_shop(shop_name):
        shop = shops.get_shop_by_name(shop_name)
        item_list = shop.get_goods_list()
        num = len(item_list)
        resp = "商店列表:\n商品名称-货币:价格-限制\n"
        i = 0
        j = 1
        for item_name in item_list:
            if i == j * 20:
                await my_backpack.send(resp + f'页数:{j} 商品总数:{i}/{num}')
                resp = ''
                j += 1
            i += 1
            index = shop.get_goods_index(item_name)
            limit = str(shop.get_goods_limit_by_index(index))
            if limit == '0':
                limit = "无限制"
            resp += f'{i}.{item_name} {shop.get_goods_coin_type_by_index(index)}:{shop.get_goods_price_by_index(index)} {limit}\n'
        await my_backpack.send(resp + f'页数:{j} 商品总数:{i}/{num}')
    else:
        await goods_list.finish(f"找不到商店名 {shop_name}")


buy_item = plugin.on_command(cmd="/购买", docs="从指定商店中购买指定数量的商品\n用法:/lk.购买 [商店名] [物品|物品*n]")


@buy_item.handle()
async def _(event: Event, matcher: Matcher, args: Message = CommandArg()):
    await is_lk_user(buy_item, event)
    if args.extract_plain_text():
        args = args.extract_plain_text().split(' ')
        index = 0
        if args[0] == '':
            index = 1
        matcher.set_arg("buy_shop_name", Message().append(args[index]))
        if len(args) > index:
            matcher.set_arg("buy_item_name", Message().append(args[index + 1]))


@buy_item.got("buy_shop_name", prompt="要从那个商店买呢？速速")
@buy_item.got("buy_item_name", prompt="要买那个商品呢？速速")
async def _(event: Event, shop_name: str = ArgPlainText("buy_shop_name"),
            item_name: str = ArgPlainText("buy_item_name")):
    if not shops.has_shop(shop_name):
        await buy_item.finish(f"找不到商店 {shop_name}")
    item_name, num = lk_util.extract_number(item_name)
    if not shops.get_shop_by_name(shop_name).has_goods(item_name):
        await buy_item.finish(f"找不到商品 {item_name}")
    if num <= 0:
        await buy_item.finish("请检查物品数量")
    result = lk_util.buy_item(event.get_user_id(), shop_name, item_name, num)
    await buy_item.finish(result)


change_name = plugin.cmd_as_group(cmd="改名", docs="用改名卡修改自己的名称")


@change_name.handle()
async def _(event: Event, matcher: Matcher, args: Message = CommandArg()):
    await is_lk_user(change_name, event)
    if args.extract_plain_text():
        matcher.set_arg("user_new_name", args)


@change_name.got("user_new_name", "新名字呢？速速")
async def _(event: Event, name: str = ArgPlainText("user_new_name")):
    if users.get_backpack(event.get_user_id()).bp_has_item("改名卡"):
        result, msg = lk_util.user_change_name(event.get_user_id(), name)
        if result:
            lk_util.item_change(event.get_user_id(), "改名卡", -1)
        await change_name.finish(msg)
    else:
        await change_name.finish("没有改名卡，请先购买")


new_things = plugin.cmd_as_group(cmd="新内容", docs="展示更新内容")


@new_things.handle()
async def _():
    await new_things.finish(LKBot.new_things)


bind = plugin.cmd_as_group(cmd='绑定', docs="为自己绑定一个名称")


@bind.handle()
async def _(matcher: Matcher, args: Message = CommandArg()):
    name = args.extract_plain_text()
    if name:
        matcher.set_arg("bind_id", args)


@bind.got("bind_id", "要绑定的名字呢？速速")
async def _(event: Event, name: str = ArgPlainText("bind_id")):
    await bind.finish(LKBot.bind(event.get_user_id(), name))


plugin_admin = Service("lk群管").document("l_o_o_k的综合性插件的群聊管理员指令部分").type(
    Service.ServiceType.SYSTEM).main_cmd("/lk")

user_list = plugin_admin.cmd_as_group(cmd='用户列表', docs='列出本群所有的用户', permission=ADMIN)


@user_list.handle()
async def _(bot: Bot, event: Event):
    group_id = int(event.group_id)
    member_list = await bot.get_group_member_list(group_id=group_id)
    members = []
    for member in member_list:
        user_id = str(member['user_id'])
        if lk_util.is_valid_user(user_id):
            members.append(user_id)
    num = len(members)
    resp = '本群用户列表:\n'
    i = 0
    j = 0
    while i < num:
        for i in range(20 + j * 20):
            if i == num:
                break
            resp += f'{i + 1}.{lk_util.get_name(members[i])}:{members[i]}\n'
        await  user_list.send(resp + f'用户总数:{i}/{num}')
        j += 1
        resp = ''


sup_bind = plugin_admin.cmd_as_group(cmd='添加绑定', docs="用法:/lk.添加绑定 @用户 [名称]\n为指定用户绑定名称",
                                     permission=ADMIN)


@sup_bind.handle()
async def _(args: Message = CommandArg()):
    if len(args) != 2:
        await sup_bind.finish('真是的，参数数量出错啦')
    if args[0].type != 'at':
        await sup_bind.finish('第一个参数为@用户啦')
    await sup_bind.finish(LKBot.bind(args[0].data['qq'], args[1].get('text')))


r18_mode_switch = plugin_admin.cmd_as_group(cmd="健康模式开关", docs="使用后更改群聊的健康模式", permission=ADMIN)


@r18_mode_switch.handle()
async def _(event: GroupMessageEvent):
    group_id = str(event.group_id)
    if lk_util.is_safe_mode_group(group_id):
        config.config.r18_groups.append(group_id)
        config.save_config()
        log.info(f'群聊{group_id}关闭健康模式by{event.get_user_id()}')
        await r18_mode_switch.finish("健康模式已关闭")
    else:
        config.config.r18_groups.remove(group_id)
        config.save_config()
        log.info(f'群聊{group_id}开启健康模式by{event.get_user_id()}')
        await r18_mode_switch.finish("健康模式已开启")


broad_new_switch = plugin_admin.cmd_as_group(cmd="尝新开关", docs="使用后更改群聊是否能使用测试内容", permission=ADMIN)


@broad_new_switch.handle()
async def _(event: GroupMessageEvent):
    group_id = str(event.group_id)
    if lk_util.is_test_group(group_id):
        config.config.test_groups.remove(group_id)
        config.save_config()
        log.info(f'群聊{group_id}关闭尝新模式by{event.get_user_id()}')
        await r18_mode_switch.finish("尝新模式已关闭")
    else:
        config.config.test_groups.append(group_id)
        config.save_config()
        log.info(f'群聊{group_id}开启尝新模式by{event.get_user_id()}')
        await r18_mode_switch.finish("尝新模式已开启")


plugin_master = Service("lk主人").document("l_o_o_k的综合性插件的主人专用指令部分").type(
    Service.ServiceType.ADMIN).main_cmd("/lk")

force_change_name = plugin_master.cmd_as_group(cmd='强制改名',
                                               docs="用法:/lk.强制改名 @用户 [名称]\n将指定用户名称修改成指定名称",
                                               permission=MASTER)


@force_change_name.handle()
async def _(args: Message = CommandArg()):
    if len(args) != 2:
        await force_change_name.finish('真是的，参数数量出错啦')
    if args[0].type != 'at':
        await force_change_name.finish('第一个参数为@用户啦')
    user_id = args[0].data['qq']
    name = lk_util.clean_str(args[1].data.get('text'))
    if not lk_util.is_valid_user(user_id):
        await force_change_name.finish('可惜捏，没找到那个人')
    _, result = lk_util.user_change_name(user_id, name)
    await force_change_name.finish(result)


all_user_list = plugin_master.cmd_as_group(cmd='所有用户', docs='列出所有的用户', permission=MASTER)


@all_user_list.handle()
async def _():
    resp = '所有用户列表:\n'
    i = 0
    j = 0
    id_list = users.get_id_list()
    num = len(id_list)
    for user_id in id_list:
        if i > 20 * (j + 1):
            await all_user_list.send(resp + f'用户总数:{i}/{num}')
            j += 1
            resp = ''
        resp += f'{i + 1}.{lk_util.get_name(user_id)}:{user_id}\n'
        i += 1
    await all_user_list.send(resp + f'用户总数:{i}/{num}')


broad_new = plugin_master.cmd_as_group(cmd="广播新内容", docs="向尝新模式的群聊广播新内容", permission=MASTER)


@broad_new.handle()
async def _(bot: Bot):
    for group in config.config.test_groups:
        await bot.send_group_msg(group_id=group, message=LKBot.broad_message)
