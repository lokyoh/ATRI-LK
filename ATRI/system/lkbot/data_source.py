from ATRI import __version__
from ATRI.message import MessageGroup, img_msg
from ATRI.system.htmlrender import md_to_pic

from .util import lk_util, PLUGIN_VERSION
from .data.user import users
from .data.item import ItemStack, items
from .data.shop import shops


class LKBot:
    new_things = f'''ATRI-LK版
{__version__} 新内容:
    - lk农场part.1
    - 更新插件
    - 重启插件
    - 插件商店插件'''
    broad_message = f'''*本群已开启尝新模式，这是新功能的人工推送*
#lk插件v{PLUGIN_VERSION}:
    !输入"/帮助 lk插件"查看具体指令!
    !输入"/lk.新内容"查看更新内容!
#lk宠物v0.1.3-fix1:
    !输入"/帮助 lk宠物"查看具体指令!
#lk农场v0.1.1:
    !输入"/帮助 lk农场"查看具体指令!'''

    @staticmethod
    def get_info(user_id):
        user = users.get_user_data(user_id)
        info = f'''用户 {user.name}:
等级:{user.lvl} 升级还需要{user.get_lvl_exp() - user.left_exp}经验
ATRI币:{user.money}
好感:{user.love}
宠物:{user.petname}'''
        return info

    @staticmethod
    def get_backpack_info(user_id):
        message_list = MessageGroup()
        backpack = users.get_backpack(user_id).get_item_list()
        resp = f"{lk_util.get_name(user_id)} 的背包:\n{'-' * 20}\n名称-类型-数量\n"
        num = len(backpack)
        i = 0
        j = 1
        for item in backpack:
            item: ItemStack
            if i == j * 20:
                message_list.add_message(resp + f"{'-' * 20}\n页数:{j} 物品总数:{i}/{num}")
                resp = ''
                j += 1
            i += 1
            resp += f'{i}.{item.get_name()}-{item.get_type().value}-{item.meta.num}\n'
        message_list.add_message(resp + f"{'-' * 20}\n页数:{j} 物品总数:{i}/{num}")
        return message_list

    @staticmethod
    def get_item_info(item_name):
        if not items.has_item(item_name):
            return f"找不到指定物品 {item_name}"
        item = items.get_item_by_name(item_name)
        return f'''{item_name}:
{item.get_item_info()}
类型:{item.get_item_type().value}
价值:{item.get_item_price_dis()}
可使用:{item.item_can_use()}'''

    @staticmethod
    def bind(user_id, name):
        name = lk_util.clean_str(name)
        if lk_util.is_valid_user(user_id):
            return f"那个...{user_id} 已绑定名称 {lk_util.get_name(user_id)} 啦"
        if len(name) > 10 or name == '':
            return "那个...名称字数超出限制10或为空"
        if not lk_util.is_valid_name(name):
            return "那个...这个名字不太合适吧"
        if users.add_user(user_id, name):
            return f"好欸！{user_id} 绑定名称 {name} 成功，咱又多了个新的朋友"
        return "那个...此名称已经被使用了，换个名字吧"

    @staticmethod
    def get_shop_list():
        message = MessageGroup()
        shop_l = shops.get_shop_names()
        num = len(shop_l)
        resp = f"商店列表:\n{'-' * 20}\n"
        i = 0
        j = 1
        for shop in shop_l:
            if i == j * 20:
                message.add_message(resp + f"{'-' * 20}\n页数:{j} 商店总数:{i}/{num}")
                resp = ''
                j += 1
            i += 1
            resp += f'{i}.{shop}\n'
        message.add_message(resp + f"{'-' * 20}\n页数:{j} 商店总数:{i}/{num}")
        return message

    @staticmethod
    async def get_goods_list(shop_name):
        message = MessageGroup()
        if not shops.has_shop(shop_name):
            return message.add_message(f"找不到商店名 {shop_name}")
        shop = shops.get_shop_by_name(shop_name)
        item_list = shop.get_goods_list()
        num = len(item_list)
        resp = f"# {shop.get_shop_name()}-商品列表:\n{shop.get_shop_info()}\n\n|编号|商品名称|货币|价格|限制|\n|:-:|:-:|:-:|:-:|:-:|\n"
        i = 0
        j = 1
        for item_name in item_list:
            if i == j * 20:
                resp += f"\n> 页数:{j} 商品总数:{i}/{num}"
                message.add_message(img_msg(await md_to_pic(resp)))
                resp = '|编号|商品名称|货币|价格|限制|\n|:-:|:-:|:-:|:-:|:-:|\n'
                j += 1
            i += 1
            index = shop.get_goods_index(item_name)
            limit = str(shop.get_goods_limit_by_index(index))
            if limit == '0':
                limit = "无限制"
            resp += f'|{i}|{item_name}|{shop.get_goods_coin_type_by_index(index)}|{shop.get_goods_price_by_index(index)}|{limit}|\n'
        resp += f"\n> 页数:{j} 商品总数:{i}/{num}"
        message.add_message(img_msg(await md_to_pic(resp)))
        return message

    @staticmethod
    async def get_group_user_list(bot, group_id):
        message = MessageGroup()
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
            message.add_message(resp + f'用户总数:{i}/{num}')
            j += 1
            resp = ''
        return message

    @staticmethod
    async def get_user_list():
        message = MessageGroup()
        resp = '所有用户列表:\n'
        i = 0
        j = 0
        id_list = users.get_id_list()
        num = len(id_list)
        for user_id in id_list:
            if i > 20 * (j + 1):
                message.add_message(resp + f'用户总数:{i}/{num}')
                j += 1
                resp = ''
            resp += f'{i + 1}.{lk_util.get_name(user_id)}:{user_id}\n'
            i += 1
        message.add_message(resp + f'用户总数:{i}/{num}')
        return message
