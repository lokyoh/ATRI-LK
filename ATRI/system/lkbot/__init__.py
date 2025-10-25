from random import choice

from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11.event import GroupMessageEvent, MessageEvent
from nonebot.adapters.onebot.v11.helpers import Cooldown
from nonebot.adapters.onebot.v11.message import Message
from nonebot.matcher import Matcher
from nonebot.params import CommandArg, ArgPlainText

from ATRI.log import log
from ATRI.permission import ADMIN, MASTER
from ATRI.service import Service

from .checker import IsLkUser
from .config import config, save_config
from .data_source import LKBot
from .util import lk_util, PLUGIN_VERSION, PLUGIN_DIR, on_startup
from .data.item import items, ItemStack
from .data.shop import shops
from .data.user import users

plugin = Service(
    "用户",
    "ATRI的综合性用户系统插件",
    PLUGIN_VERSION,
    Service.ServiceType.LKPLUGIN
)

_lmt_notice = ["慢...慢一..点❤", "冷静1下", "歇会歇会~~", "呜呜...别急", "太快了...受不了", "不要这么快呀"]

my_info = plugin.on_command(cmd="/我的信息", docs="查询自己的信息")


@my_info.handle([IsLkUser])
async def _(event: MessageEvent):
    await my_info.finish(LKBot.get_info(event.get_user_id()))


my_backpack = plugin.on_command(cmd="/我的背包", docs="查看背包中的内容")


@my_backpack.handle([IsLkUser])
async def _(event: MessageEvent):
    await LKBot.get_backpack_info(event.get_user_id()).send_message(my_backpack)


item_inquiry = plugin.on_command(cmd="/物品查询", docs="查询指定物品信息")


@item_inquiry.handle()
async def _(matcher: Matcher, args: Message = CommandArg()):
    if args.extract_plain_text():
        matcher.set_arg("item_inquiry_name", args)


@item_inquiry.got("item_inquiry_name", prompt="要查询的物品呢？速速")
async def _(item_name=ArgPlainText("item_inquiry_name")):
    await item_inquiry.finish(LKBot.get_item_info(lk_util.clean_str(item_name)))


use_item = plugin.on_command(cmd="/使用", docs="使用指定物品,'全部物品'使用全部,'物品*n'使用n个物品")


@use_item.handle([IsLkUser])
async def _(matcher: Matcher, args: Message = CommandArg()):
    if args.extract_plain_text():
        matcher.set_arg("use_item_name", args)


@use_item.got("use_item_name", prompt="要使用的物品呢？速速")
async def _(event: MessageEvent, item_name=ArgPlainText("use_item_name")):
    item_name = lk_util.clean_str(item_name)
    item_name, num = lk_util.extract_number(item_name)
    if not items.has_item(item_name):
        await use_item.finish(f"找不到指定物品 {item_name}")
    if num <= 0 and num != -1:
        return use_item.finish("数量不符合规范")
    have_used, msg = lk_util.use_item(event.get_user_id(), item_name, num)
    if have_used:
        await use_item.finish(f"使用信息:\n{msg}")
    else:
        await use_item.finish(f"使用失败:\n{msg}")


recycle_item = plugin.on_command(cmd="/回收", docs="将指定数量物品换成ATRI币,'全部物品'回收全部,'物品*n'回收n个物品")


@recycle_item.handle([IsLkUser])
async def _(matcher: Matcher, args: Message = CommandArg()):
    if args.extract_plain_text():
        matcher.set_arg("recycle_item", args)


@recycle_item.got("recycle_item", prompt="要回收的物品呢？速速")
async def _(event: MessageEvent, item_name=ArgPlainText("recycle_item")):
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


shop_list = plugin.on_command(cmd="/商店列表", docs="列出所有商店")


@shop_list.handle()
async def _():
    await LKBot.get_shop_list().send_message(shop_list)


goods_list = plugin.on_command(cmd="/商品列表", docs="列出指定商店的商品列表")


@goods_list.handle([Cooldown(5, prompt=choice(_lmt_notice))])
async def _(matcher: Matcher, args: Message = CommandArg()):
    if args.extract_plain_text():
        matcher.set_arg("shop_name", args)


@goods_list.got("shop_name", prompt="要浏览那个商店呢?速速")
async def _(shop_name=ArgPlainText("shop_name")):
    shop_name = lk_util.clean_str(shop_name)
    mg = await LKBot.get_goods_list(shop_name)
    await mg.send_message(goods_list)


buy_item = plugin.on_command(cmd="/购买", docs="从指定商店中购买指定数量的商品\n用法:/购买 [商店名] [物品|物品*n]")


@buy_item.handle([IsLkUser])
async def _(matcher: Matcher, args: Message = CommandArg()):
    if args.extract_plain_text():
        args = args.extract_plain_text().split(' ')
        index = 0
        if args[0] == '':
            index = 1
        matcher.set_arg("buy_shop_name", Message().append(args[index]))
        if len(args) > index + 1:
            matcher.set_arg("buy_item_name", Message().append(args[index + 1]))


@buy_item.got("buy_shop_name", prompt="要从那个商店买呢？速速")
@buy_item.got("buy_item_name", prompt="要买那个商品呢？速速")
async def _(event: MessageEvent, shop_name: str = ArgPlainText("buy_shop_name"),
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


change_name = plugin.on_command(cmd="/改名", docs="用改名卡修改自己的名称")


@change_name.handle([IsLkUser])
async def _(matcher: Matcher, args: Message = CommandArg()):
    if args.extract_plain_text():
        matcher.set_arg("user_new_name", args)


@change_name.got("user_new_name", "新名字呢？速速")
async def _(event: MessageEvent, name: str = ArgPlainText("user_new_name")):
    user_id = event.get_user_id()
    if lk_util.item_change(user_id, "改名卡", -1):
        result, msg = lk_util.user_change_name(user_id, name)
        if not result:
            lk_util.item_change(user_id, "改名卡", 1)
        await change_name.finish(msg)
    else:
        await change_name.finish("没有改名卡，请先购买")


bind = plugin.on_command(cmd='/绑定', docs="为自己绑定一个名称")


@bind.handle()
async def _(matcher: Matcher, args: Message = CommandArg()):
    name = args.extract_plain_text()
    if name:
        matcher.set_arg("bind_id", args)


@bind.got("bind_id", "要绑定的名字呢？速速")
async def _(event: MessageEvent, name: str = ArgPlainText("bind_id")):
    await bind.finish(LKBot.bind(event.get_user_id(), name))


rank = plugin.on_command("/排行", "查看排行")


@rank.handle()
async def _(matcher: Matcher, args: Message = CommandArg()):
    name = args.extract_plain_text()
    if name:
        matcher.set_arg("rank_name", args)


@rank.got("rank_name", "你要查看 经验 好感 ATRI币 中的哪个排行呢")
async def _(name: str = ArgPlainText("rank_name")):
    msg = await LKBot.get_rank(name)
    await rank.finish(msg)


plugin_admin = Service("群管").document("ATRI的综合性插件的群聊管理员指令部分").type(
    Service.ServiceType.LKPLUGIN).version(PLUGIN_VERSION).permission(ADMIN)

user_list = plugin_admin.on_command(cmd='/用户列表', docs='列出本群所有的用户', permission=ADMIN)


@user_list.handle()
async def _(bot: Bot, event: MessageEvent):
    mg = await LKBot.get_group_user_list(bot, int(event.group_id))
    await mg.send_message(user_list)


sup_bind = plugin_admin.on_command(cmd='/添加绑定', docs="用法:/添加绑定 @用户 [名称]\n为指定用户绑定名称",
                                   permission=ADMIN)


@sup_bind.handle()
async def _(args: Message = CommandArg()):
    if len(args) != 2:
        await sup_bind.finish('真是的，参数数量出错啦')
    if args[0].type != 'at':
        await sup_bind.finish('第一个参数为@用户啦')
    await sup_bind.finish(LKBot.bind(args[0].data['qq'], args[1].data.get('text', '')))


r18_mode_switch = plugin_admin.on_command(cmd="/健康模式开关", docs="使用后更改群聊的健康模式", permission=ADMIN)


@r18_mode_switch.handle()
async def _(event: GroupMessageEvent):
    group_id = str(event.group_id)
    if lk_util.is_safe_mode_group(group_id):
        config.r18_groups.append(group_id)
        save_config()
        log.info(f'群聊{group_id}关闭健康模式by{event.get_user_id()}')
        await r18_mode_switch.finish("健康模式已关闭")
    else:
        config.r18_groups.remove(group_id)
        save_config()
        log.info(f'群聊{group_id}开启健康模式by{event.get_user_id()}')
        await r18_mode_switch.finish("健康模式已开启")


broad_new_switch = plugin_admin.on_command(cmd="/尝新开关", docs="使用后更改群聊是否能使用测试内容", permission=ADMIN)


@broad_new_switch.handle()
async def _(event: GroupMessageEvent):
    group_id = str(event.group_id)
    if lk_util.is_test_group(group_id):
        config.test_groups.remove(group_id)
        save_config()
        log.info(f'群聊{group_id}关闭尝新模式by{event.get_user_id()}')
        await r18_mode_switch.finish("尝新模式已关闭")
    else:
        config.test_groups.append(group_id)
        save_config()
        log.info(f'群聊{group_id}开启尝新模式by{event.get_user_id()}')
        await r18_mode_switch.finish("尝新模式已开启")


plugin_master = Service("主人").document("ATRI的综合性插件的主人专用指令部分").type(
    Service.ServiceType.LKPLUGIN).version(PLUGIN_VERSION).permission(MASTER)

force_change_name = plugin_master.on_command(cmd='/强制改名',
                                             docs="用法:/强制改名 @用户 [名称]\n将指定用户名称修改成指定名称",
                                             permission=MASTER)


@force_change_name.handle()
async def _(args: Message = CommandArg()):
    if len(args) != 2:
        await force_change_name.finish('真是的，参数数量出错啦')
    if args[0].type != 'at':
        await force_change_name.finish('第一个参数为@用户啦')
    user_id = args[0].data['qq']
    name = lk_util.clean_str(args[1].data.get('text', ''))
    if not lk_util.is_valid_user(user_id):
        await force_change_name.finish('可惜捏，没找到那个人')
    _, result = lk_util.user_change_name(user_id, name)
    await force_change_name.finish(result)


all_user_list = plugin_master.on_command(cmd='/所有用户', docs='列出所有的用户', permission=MASTER)


@all_user_list.handle()
async def _():
    mg = LKBot.get_user_list()
    await mg.send_message(all_user_list)


plugin.on_startup(on_startup)
