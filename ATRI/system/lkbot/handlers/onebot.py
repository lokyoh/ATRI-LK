from nonebot.adapters import Message as BaseMessage
from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11.event import (
    GroupMessageEvent,
    MessageEvent,
    PrivateMessageEvent,
)
from nonebot.adapters.onebot.v11.message import Message
from nonebot.adapters.qq import (
    C2CMessageCreateEvent,
    DirectMessageCreateEvent,
    GroupAtMessageCreateEvent,
)
from nonebot.adapters.qq import MessageEvent as QQMessageEvent
from nonebot.matcher import Matcher
from nonebot.params import ArgPlainText, CommandArg

from ATRI.bot import BotUtils
from ATRI.log import log
from ATRI.message import QQMessage

from ..checker import IsLkUser
from ..config import config
from ..data.item import items
from ..data.shop import shops
from ..data_source import LKBot
from ..matchers import (
    account_info,
    all_user_list,
    bind,
    bind_existing_account,
    broad_new_switch,
    buy_item,
    change_name,
    force_bind_account,
    force_change_name,
    force_unbind_account,
    get_binding_secret,
    goods_list,
    item_inquiry,
    my_backpack,
    my_info,
    r18_mode_switch,
    rank,
    recycle_item,
    shop_list,
    sup_bind,
    unbind_account,
    use_item,
    user_list,
)
from ..qq import BIND_TIP
from ..util import lk_util

cmd_start = BotUtils.get_command_start()


@my_info.handle([IsLkUser])
async def _(event: MessageEvent | QQMessageEvent):
    await my_info.finish(LKBot.get_info(event.get_user_id()))


@my_backpack.handle([IsLkUser])
async def _(event: MessageEvent | QQMessageEvent):
    await LKBot.get_backpack_info(event.get_user_id()).send_message(my_backpack)


@item_inquiry.handle()
async def _(matcher: Matcher, args: BaseMessage = CommandArg()):
    if args.extract_plain_text():
        matcher.set_arg("item_inquiry_name", args)


@item_inquiry.got("item_inquiry_name", prompt="要查询的物品呢？速速")
async def _(item_name=ArgPlainText("item_inquiry_name")):
    await item_inquiry.finish(LKBot.get_item_info(lk_util.clean_str(item_name)))


@use_item.handle([IsLkUser])
async def _(matcher: Matcher, args: BaseMessage = CommandArg()):
    if args.extract_plain_text():
        matcher.set_arg("use_item_name", args)


@use_item.got("use_item_name", prompt="要使用的物品呢？速速")
async def _(
    event: MessageEvent | QQMessageEvent, item_name=ArgPlainText("use_item_name")
):
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


@recycle_item.handle([IsLkUser])
async def _(matcher: Matcher, args: BaseMessage = CommandArg()):
    if args.extract_plain_text():
        matcher.set_arg("recycle_item", args)


@recycle_item.got("recycle_item", prompt="要回收的物品呢？速速")
async def _(
    event: MessageEvent | QQMessageEvent, item_name=ArgPlainText("recycle_item")
):
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


@shop_list.handle()
async def _():
    await LKBot.get_shop_list().send_message(shop_list)


@goods_list.handle()
async def _(matcher: Matcher, args: BaseMessage = CommandArg()):
    if args.extract_plain_text():
        matcher.set_arg("shop_name", args)


@goods_list.got("shop_name", prompt="要浏览那个商店呢?速速")
async def _(event: MessageEvent | QQMessageEvent, shop_name=ArgPlainText("shop_name")):
    shop_name = lk_util.clean_str(shop_name)
    mtype = 0
    if isinstance(event, QQMessageEvent):
        mtype = 1
    mg = await LKBot.get_goods_list(shop_name, mtype)
    await mg.send_message(goods_list)


@buy_item.handle([IsLkUser])
async def _(matcher: Matcher, args: BaseMessage = CommandArg()):
    if args.extract_plain_text():
        args = args.extract_plain_text().split(" ")
        index = 0
        if args[0] == "":
            index = 1
        matcher.set_arg("buy_shop_name", Message().append(args[index]))
        if len(args) > index + 1:
            matcher.set_arg("buy_item_name", Message().append(args[index + 1]))


@buy_item.got("buy_shop_name", prompt="要从那个商店买呢？速速")
@buy_item.got("buy_item_name", prompt="要买那个商品呢？速速")
async def _(
    event: MessageEvent | QQMessageEvent,
    shop_name: str = ArgPlainText("buy_shop_name"),
    item_name: str = ArgPlainText("buy_item_name"),
):
    if not shops.has_shop(shop_name):
        await buy_item.finish(f"找不到商店 {shop_name}")
    item_name, num = lk_util.extract_number(item_name)
    if not shops.get_shop_by_name(shop_name).has_goods(item_name):
        await buy_item.finish(f"找不到商品 {item_name}")
    if num <= 0:
        await buy_item.finish("请检查物品数量")
    result = lk_util.buy_item(event.get_user_id(), shop_name, item_name, num)
    await buy_item.finish(result)


@change_name.handle([IsLkUser])
async def _(matcher: Matcher, args: BaseMessage = CommandArg()):
    if args.extract_plain_text():
        matcher.set_arg("user_new_name", args)


@change_name.got("user_new_name", "新名字呢？速速")
async def _(
    event: MessageEvent | QQMessageEvent, name: str = ArgPlainText("user_new_name")
):
    user_id = event.get_user_id()
    if lk_util.item_change(user_id, "改名卡", -1):
        result, msg = lk_util.user_change_name(user_id, name)
        if not result:
            lk_util.item_change(user_id, "改名卡", 1)
        await change_name.finish(msg)
    else:
        if isinstance(event, QQMessageEvent):
            await change_name.finish(
                QQMessage.md_msg(
                    f"没有改名卡，请先{QQMessage.cmd(f'{cmd_start}购买 基础商店 改名卡', '购买')}"
                )
            )
        await change_name.finish("没有改名卡，请先购买")


@bind.handle()
async def _(matcher: Matcher, args: BaseMessage = CommandArg()):
    name = args.extract_plain_text()
    if name:
        matcher.set_arg("bind_id", args)


@bind.got("bind_id", "要绑定的名字呢？速速")
async def _(event: MessageEvent | QQMessageEvent, name: str = ArgPlainText("bind_id")):
    await bind.finish(LKBot.bind(event.get_user_id(), name))


@get_binding_secret.handle()
async def _(event: MessageEvent | QQMessageEvent):
    if not isinstance(
        event,
        (PrivateMessageEvent, C2CMessageCreateEvent, DirectMessageCreateEvent),
    ):
        await get_binding_secret.finish("请私聊获取绑定密钥")
    if not lk_util.is_valid_user(event.get_user_id()):
        if isinstance(event, QQMessageEvent):
            await get_binding_secret.finish(QQMessage.md_msg(BIND_TIP))
        await get_binding_secret.finish(lk_util.bind_tip)
    await get_binding_secret.finish(LKBot.get_binding_secret(event.get_user_id()))


@bind_existing_account.handle()
async def _(event: MessageEvent | QQMessageEvent):
    secret = event.get_plaintext()[len(f"{cmd_start}绑定已有账号") :].strip()
    if not secret:
        await bind_existing_account.finish(f"用法：{cmd_start}绑定已有账号 [绑定密钥]")
    await bind_existing_account.finish(
        LKBot.bind_existing_account(event.get_user_id(), secret)
    )


@unbind_account.handle()
async def _(event: MessageEvent | QQMessageEvent):
    await unbind_account.finish(LKBot.unbind_account(event.get_user_id()))


@rank.handle()
async def _(matcher: Matcher, args: BaseMessage = CommandArg()):
    name = args.extract_plain_text()
    if name:
        matcher.set_arg("rank_name", args)


@rank.got("rank_name", "你要查看 经验 好感 ATRI币 中的哪个排行呢")
async def _(name: str = ArgPlainText("rank_name")):
    msg = await LKBot.get_rank(name)
    await rank.finish(msg)


@account_info.handle()
async def _(event: MessageEvent | QQMessageEvent):
    user_id = event.get_user_id()
    group_id = str(getattr(event, "group_id", None))
    await account_info.send(
        f"用户ID:{user_id}{f'\n组ID:{group_id}' if group_id else ''}"
    )


@user_list.handle()
async def _(bot: Bot, event: GroupMessageEvent | GroupAtMessageCreateEvent):
    mg = await LKBot.get_group_user_list(bot, int(event.group_id))
    await mg.send_message(user_list)


@sup_bind.handle()
async def _(args: Message = CommandArg()):
    if len(args) != 2:
        await sup_bind.finish("真是的，参数数量出错啦")
    if args[0].type != "at":
        await sup_bind.finish("第一个参数为@用户啦")
    await sup_bind.finish(LKBot.bind(args[0].data["qq"], args[1].data.get("text", "")))


@r18_mode_switch.handle()
async def _(event: GroupMessageEvent):
    group_id = str(event.group_id)
    r18_groups: list = config.get("r18_groups")
    state = "开启"
    if lk_util.is_safe_mode_group(group_id):
        r18_groups.append(group_id)
        state = "关闭"
    else:
        r18_groups.remove(group_id)
    config.set("r18_groups", r18_groups)
    log.info(f"群聊{group_id}{state}健康模式by{event.get_user_id()}")
    await r18_mode_switch.finish(f"健康模式已{state}")


@broad_new_switch.handle()
async def _(event: GroupMessageEvent):
    group_id = str(event.group_id)
    test_groups: list = config.get("test_groups")
    state = "开启"
    if lk_util.is_test_group(group_id):
        test_groups.remove(group_id)
        state = "关闭"
    else:
        test_groups.append(group_id)
    config.set("r18_groups", test_groups)
    log.info(f"群聊{group_id}{state}尝新模式by{event.get_user_id()}")
    await broad_new_switch.finish(f"尝新模式已{state}")


@force_change_name.handle()
async def _(args: Message = CommandArg()):
    if len(args) != 2:
        await force_change_name.finish("真是的，参数数量出错啦")
    if args[0].type != "at":
        await force_change_name.finish("第一个参数为@用户啦")
    user_id = args[0].data["qq"]
    name = lk_util.clean_str(args[1].data.get("text", ""))
    if not lk_util.is_valid_user(user_id):
        await force_change_name.finish("可惜捏，没找到那个人")
    _, result = lk_util.user_change_name(user_id, name)
    await force_change_name.finish(result)


@all_user_list.handle()
async def _():
    mg = LKBot.get_user_list()
    await mg.send_message(all_user_list)


@force_bind_account.handle()
async def _(args: BaseMessage = CommandArg()):
    arguments = args.extract_plain_text().split()
    if len(arguments) != 2:
        await force_bind_account.finish(
            "用法：强制绑定已有账号 [目标用户ID] [主账号ID]"
        )
    await force_bind_account.finish(
        LKBot.force_bind_account(arguments[0], arguments[1])
    )


@force_unbind_account.handle()
async def _(args: BaseMessage = CommandArg()):
    arguments = args.extract_plain_text().split()
    if len(arguments) != 1:
        await force_unbind_account.finish("用法：强制解绑 [目标用户ID]")
    await force_unbind_account.finish(LKBot.force_unbind_account(arguments[0]))
