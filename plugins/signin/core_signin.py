import os
from datetime import datetime, date
from io import BytesIO
from PIL import Image

from nonebot.adapters.onebot.v11.event import GroupMessageEvent
from nonebot.exception import FinishedException

from ATRI.exceptions import str_traceback
from ATRI.log import log
from ATRI.message import MessageBuilder
from ATRI.utils.curve import IntToBoolRandom
from ATRI.utils.img_editor import IMGEditor, get_image_bytes
from ATRI.system.lkapi.bot import util as lk_util, PLUGIN_DIR
from ATRI.system.lkapi.utils.picture import get_pic_from
from ATRI.system.lkapi.entity.user import get_user_data, sign

from .data_source import signin, Signin


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
    user_data = get_user_data(user_id)
    if r18_mode:
        my_random = IntToBoolRandom(80, 200)
        if my_random.get_result(int(user_data.love / 100) + user_data.lvl):
            src = 'lolicon_r18'
        try:
            image_content = await get_pic_from(src)
            image = Image.open(BytesIO(image_content))
        except Exception as e:
            log.warning(f'获取图片失败:\n{str_traceback(e)}')
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
     .add_text(30, 590, f'经验: {user_data.left_exp} / {user_data.get_lvl_exp()}', 25)
     .add_text(30, 640, f'ATRI币: {user_data.money}', 25)
     .add_text(30, 690, f'好感: {user_data.love}', 25)
     .add_text(30, 740, f'日期: {datetime.now().strftime("%Y年%m月%d日 %H:%M")}', 25)
     .save_rgb(save_path)
     )
    return save_path


class CoreSignin(Signin):
    @staticmethod
    async def signin(event, matcher):
        r18_mode = not lk_util.is_safe_mode_group(event.group_id) if type(event) is GroupMessageEvent else True
        user_id = event.get_user_id()
        msg = ''
        try:
            message = MessageBuilder().at(user_id)
            _, msg = sign(user_id)
            message.text(msg)
            log.info(f'{user_id}签到 r18:{r18_mode}, {msg}')
            img_path = await get_pic(user_id, r18_mode=r18_mode)
            message.image(get_image_bytes(img_path))
            await matcher.finish(message)
        except FinishedException:
            raise
        except Exception as e:
            if r18_mode:
                path = os.path.join(PLUGIN_DIR, 'sign_in', 'r18', f"{user_id}.jpg")
            else:
                path = os.path.join(PLUGIN_DIR, 'sign_in', f"{user_id}.jpg")
            if os.path.exists(path):
                os.remove(path)
            log.warning(f"签到发生错误:\n{str_traceback(e)}")
            message = MessageBuilder().at(user_id)
            user_data = get_user_data(user_id)
            message.text(f'签到成功,你已签到{user_data.signdays}天{msg}')
            await matcher.finish(message)


signin.change_signin(CoreSignin)
