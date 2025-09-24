import os
from datetime import datetime, date

from nonebot.exception import FinishedException

from ATRI.exceptions import str_traceback
from ATRI.log import log
from ATRI.message import MessageBuilder
from ATRI.utils.curve import IntToBoolRandom
from ATRI.utils.img_editor import IMGEditor, get_image_bytes
from ATRI.system.lkapi.bot import PLUGIN_DIR
from ATRI.system.lkapi.utils.picture import get_pic_from, has_source
from ATRI.system.lkapi.entity.user import get_user_data, sign

from . import config
from .config import SignInConfig
from .data_source import signin, Signin

_config: SignInConfig = config.config()


async def get_pic(user_id, group_id):
    """获取签到卡片"""
    src = _config.base_source
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
    my_random = IntToBoolRandom(80, 200)
    if my_random.get_result(int(user_data.love / 100) + user_data.lvl):
        src = _config.unique_source
    try:
        if not has_source(src):
            log.warning(f'{src}图片源不存在')
        image = await get_pic_from(src, group_id)
    except Exception as e:
        log.warning(f'获取图片失败:\n{str_traceback(e)}')
        return await get_pic(user_id, group_id)
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
        user_id = event.get_user_id()
        group_id = str(getattr(event, 'group_id', None))
        try:
            message = MessageBuilder().text('')
            _, msg = sign(user_id)
            for m in msg:
                message.auto_append(m)
            log.info(f'{user_id}签到')
            img_path = await get_pic(user_id, group_id)
            message.image(get_image_bytes(img_path))
            await matcher.finish(message, at_sender=True)
        except FinishedException:
            raise
        except Exception as e:
            path = os.path.join(PLUGIN_DIR, 'sign_in', f"{user_id}.jpg")
            if os.path.exists(path):
                os.remove(path)
            log.warning(f"签到发生错误:\n{str_traceback(e)}")
            message = MessageBuilder()
            user_data = get_user_data(user_id)
            message.text(f'签到成功,你已签到{user_data.signdays}天')
            await matcher.finish(message, at_sender=True)


signin.change_signin(CoreSignin)
