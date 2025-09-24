import os
from random import choice
from datetime import datetime, date

from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.adapters.onebot.v11.helpers import Cooldown

from ATRI.message import img_msg_from_path, MessageBuilder
from ATRI.utils.img_editor import IMGEditor
from ATRI.system.lkapi.bot import PLUGIN_DIR, util as lk_util
from ATRI.system.lkapi.entity.user import sign
from ATRI.system.lkapi.utils.picture import get_pic_from
from ATRI.service import Service

from .fortune_data import get_fortune

plugin = Service(
    '运势',
    '亚托莉的运势插件',
    '0.1.3',
    Service.ServiceType.ENTERTAINMENT
)

today_fortune = plugin.on_command('今日运势', '今日运势', aliases={'运势'})


@today_fortune.handle([Cooldown(60, prompt='今日运势已经发送了哦')])
async def _(event: MessageEvent):
    user_id = event.get_user_id()
    message = MessageBuilder().text('')
    if lk_util.is_valid_user(user_id):
        state, msg = sign(user_id)
        if state:
            message.text(f'今天尚未签到，已自动签到：')
            for m in msg:
                message.auto_append(m)
    else:
        message.text(f'{lk_util.bind_tip}才能够自动签到哦!')
    message.auto_append(await get_pic(user_id))
    await today_fortune.finish(message, at_sender=user_id)


async def get_pic(user_id):
    """获取签到卡片"""
    save_dir = os.path.join(PLUGIN_DIR, 'fortune')
    save_path = os.path.join(save_dir, f"{user_id}.jpg")
    if os.path.exists(save_path):
        modification_time = os.path.getmtime(save_path)
        modification_date = date.fromtimestamp(modification_time)
        today_date = date.today()
        if modification_date == today_date:
            return img_msg_from_path(save_path)
    os.makedirs(save_dir, exist_ok=True)
    f = get_fortune()
    (IMGEditor(await get_pic_from('local'))
     .resize(450, 800)
     .add_rectangle(10, 350, 430, 440, 192, 10)
     .add_text(30, 370, f'您的今日运势为:', 30)
     .add_middle_text(225, 410, f["fortune"], 50)
     .add_middle_text(225, 465, f'{'★' * f['level']}{'☆' * (5 - f['level'])}', 50)
     .add_auto_text(30, 520, f'{choice(f["content"])}', 25, max_y=730, max_width=400, vertical_align='center')
     .add_middle_text(225, 735, f'日期: {datetime.now().strftime("%Y年%m月%d日 %H:%M")}', 25)
     .add_middle_text(225, 765, '仅供娱乐 相信科学 请勿迷信', 20)
     .save_rgb(save_path)
     )
    return img_msg_from_path(save_path)
