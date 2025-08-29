import os
from random import choice
from datetime import datetime, date

from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.adapters.onebot.v11.helpers import Cooldown

from ATRI.message import img_msg
from ATRI.utils.img_editor import IMGEditor, get_image_bytes
from ATRI.system.lkapi.bot import PLUGIN_DIR
from ATRI.system.lkapi.bot.checker import IsLkUser
from ATRI.system.lkapi.entity.user import sign
from ATRI.system.lkapi.utils.picture import get_pic_from
from ATRI.service import Service

from .fortune_data import get_fortune

plugin = Service(
    '运势',
    '亚托莉的运势插件',
    '0.1.1',
    Service.ServiceType.LKPLUGIN
)

today_fortune = plugin.on_command('/今日运势', '今日运势', aliases={'/运势'})


@today_fortune.handle([IsLkUser, Cooldown(60, prompt='今日运势已经发送了哦')])
async def _(event: MessageEvent):
    user_id = event.get_user_id()
    state, msg = sign(user_id)
    if state:
        await today_fortune.send(f'今天尚未签到，已自动签到：{msg}')
    await today_fortune.finish(await get_pic(user_id), at_sender=True)


async def get_pic(user_id):
    """获取签到卡片"""
    save_dir = os.path.join(PLUGIN_DIR, 'fortune')
    save_path = os.path.join(save_dir, f"{user_id}.jpg")
    if os.path.exists(save_path):
        modification_time = os.path.getmtime(save_path)
        modification_date = date.fromtimestamp(modification_time)
        today_date = date.today()
        if modification_date == today_date:
            return img_msg(get_image_bytes(save_path))
    image = await get_pic_from('local')
    os.makedirs(save_dir, exist_ok=True)
    f = get_fortune()
    (IMGEditor(image)
     .resize(450, 800)
     .add_rectangle(10, 350, 430, 440, 192, 10)
     .add_text(30, 370, f'您的今日运势为:', 30)
     .add_middle_text(225, 405, f["fortune"], 50)
     .add_middle_text(225, 460, f'{'★' * f['level']}{'☆' * (5 - f['level'])}', 50)
     .add_auto_text(30, 515, f'{choice(f["content"])}', 25, max_y=710, max_width=400, vertical_align='center')
     .add_middle_text(225, 715, '仅供娱乐 相信科学 请勿迷信', 20)
     .add_middle_text(225, 740, f'日期: {datetime.now().strftime("%Y年%m月%d日 %H:%M")}', 25)
     .save_rgb(save_path)
     )
    return img_msg(get_image_bytes(save_path))
