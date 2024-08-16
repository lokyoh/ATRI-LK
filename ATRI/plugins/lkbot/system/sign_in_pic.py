import os
from datetime import datetime, date
from io import BytesIO
from PIL import Image

from ATRI.log import log
from ATRI.utils.curve import IntToBoolRandom
from ATRI.utils.img_editor import IMGEditor

from .data.user import users
from .tools.get_pic import get_pic_from
from ..util import PLUGIN_DIR


async def get_pic(user_id, r18_mode: bool = False, src: str = 'lolicon'):
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
            src = 'local'
            image = await get_pic_from(src)
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
