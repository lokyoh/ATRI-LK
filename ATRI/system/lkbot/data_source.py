import os
from datetime import datetime, date
from io import BytesIO
from PIL import Image

from nonebot.adapters.onebot.v11 import MessageSegment, Message

from ATRI import __version__
from ATRI.log import log
from ATRI.utils.curve import IntToBoolRandom
from ATRI.utils.img_editor import get_image_bytes, IMGEditor

from .util import lk_util, PLUGIN_VERSION, sign_in_event, PLUGIN_DIR
from .data.user import users
from .tools.get_pic import get_pic_from


class LKBot:
    new_things = f'''ATRI-LK版
{__version__} 新内容:
    - lk农场part.1'''
    broad_message = f'''*本群已开启尝新模式，这是新功能的人工推送*
#lk插件v{PLUGIN_VERSION}:
    !输入"/帮助 lk插件"查看具体指令!
    !输入"/lk.新内容"查看更新内容!
#lk宠物v0.1.3-fix1:
    !输入"/帮助 lk宠物"查看具体指令!
#组队插件1.0.0-fix2-正式版：
    !输入"/帮助 组队插件"查看具体指令!'''

    @staticmethod
    async def sign_in(user_id, r18_mode):
        message = Message().append(MessageSegment.at(user_id))
        sign_result = users.sign(user_id)
        if sign_result:
            msg = sign_in_event.notify(user_id)
            message.append(msg)
        else:
            message.append("\n今日已签到")
        img_path = await get_pic(user_id, r18_mode=r18_mode)
        log.info(f'{user_id}签到 r18:{r18_mode}')
        message.append(MessageSegment.image(get_image_bytes(img_path)))
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
    def bind(user_id, name):
        name = lk_util.clean_str(name)
        if lk_util.is_valid_user(user_id):
            return f"那个...{user_id} 已绑定名称 {lk_util.get_name(user_id)} 啦"
        else:
            if len(name) > 10 or name == '':
                return "那个...名称字数超出限制10或为空"
            if not lk_util.is_valid_name(name):
                return "那个...这个名字不太合适吧"
            if users.add_user(user_id, name):
                return f"好欸！{user_id} 绑定名称 {name} 成功，咱又多了个新的朋友"
            return "那个...此名称已经被使用了，换个名字吧"


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
            log.info(f"{modification_date}->{today_date}")
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
