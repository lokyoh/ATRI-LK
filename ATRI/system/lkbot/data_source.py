import os
from datetime import datetime, date
from io import BytesIO
from PIL import Image

from ATRI import __version__
from ATRI.log import log
from ATRI.message import MessageBuilder, MessageGroup, img_msg
from ATRI.utils.curve import IntToBoolRandom
from ATRI.utils.img_editor import get_image_bytes, IMGEditor
from ATRI.system.htmlrender import md_to_pic

from .util import lk_util, PLUGIN_VERSION, sign_in_event, PLUGIN_DIR
from .data.user import users
from .data.item import ItemStack, items
from .data.shop import shops
from .tools.get_pic import get_pic_from


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
    async def sign_in(user_id, r18_mode):
        message = MessageBuilder().at(user_id)
        sign_result = users.sign(user_id)
        if sign_result:
            msg = sign_in_event.notify(user_id)
            message.text(msg)
        else:
            message.text("今日已签到")
        img_path = await get_pic(user_id, r18_mode=r18_mode)
        log.info(f'{user_id}签到 r18:{r18_mode}')
        message.image(get_image_bytes(img_path))
        return message

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


async def get_pic(user_id, r18_mode: bool = False, src: str = 'lolicon'):
    """获取签到卡片"""
    if r18_mode:
        save_dir = os.path.join(PLUGIN_DIR, 'sign_in', 'r18')
    else:
        save_dir = os.path.join(PLUGIN_DIR, 'sign_in')
    save_path = os.path.join(save_dir, f"{user_id}.jpg")
    if os.path.exists(save_path):
        modification_time = os.path.getmtime(save_path)
        modification_date = date.fromtimestamp(modification_time)
        today_date = date.today()
        if modification_date == today_date:
            return save_path
        else:
            log.debug(f"{user_id}签到日期变化:{modification_date}->{today_date}")
    user_data = users.get_user_data(user_id)
    if r18_mode:
        my_random = IntToBoolRandom(80, 200)
        if my_random.get_result(int(user_data.love / 100) + user_data.lvl):
            src = 'lolicon_r18'
        try:
            image_content = await get_pic_from(src)
            image = Image.open(BytesIO(image_content))
        except Exception as e:
            log.warning(f'{e}:\n{e.args}')
            return await get_pic(user_id)
    else:
        src = 'local'
        image = await get_pic_from(src)
    os.makedirs(save_dir, exist_ok=True)
    (IMGEditor(image)
     .resize(450, 800)
     .add_rectangle(10, 350, 430, 440, 192, 10)
     .add_middle_text(225, 370, f'{user_data.name}', 50)
     .add_text(30, 450, f'签到成功！--{src}', 35)
     .add_right_text(420, 500, f'--你已签到{user_data.signdays}天', 25)
     .add_text(30, 540, f'等级: {user_data.lvl}', 25)
     .add_text(30, 580, f'经验: {user_data.left_exp} / {user_data.get_lvl_exp()}', 25)
     .add_text(30, 620, f'ATRI币: {user_data.money}', 25)
     .add_text(30, 660, f'好感: {user_data.love}', 25)
     .add_text(30, 700, f'宠物: {user_data.petname:}', 25)
     .add_text(30, 740, f'日期: {datetime.now().strftime("%Y年%m月%d日 %H:%M")}', 25)
     .save_rgb(save_path)
     )
    return save_path
