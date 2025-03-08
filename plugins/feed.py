from random import choice
from datetime import datetime

from nonebot.adapters.onebot.v11 import Event
from nonebot.adapters.onebot.v11.helpers import Cooldown
from nonebot.params import Depends

from ATRI.service import Service
from ATRI.message import MessageBuilder
from ATRI.system.lkbot.checker import is_lk_user
from ATRI.system.lkbot.data.user import users, lk_db

plugin = Service(
    "投喂",
    "向可爱的亚托利投喂食物",
    "0.1.1",
    Service.ServiceType.LKPLUGIN
)

_lmt_notice = ["慢...慢一..点❤", "冷静1下", "歇会歇会~~", "呜呜...别急", "太快了...受不了", "不要这么快呀"]

feed = plugin.on_command("投食", "向可爱的亚托利投喂食物", aliases={'投喂', '投喂食物'})


def update_feed_db(connection, version):
    cursor = connection.cursor()
    if version < 1:
        pass
    cursor.close()


feed_db = lk_db.get_table("LKFEEDDATA", '''
        ID          INTEGER PRIMARY KEY,
        DATE        TEXT    DEFAULT '2000-01-01'
        ''', 0, update_feed_db)

love_num = 1


def feed_func(user_id):
    message = MessageBuilder().at(user_id)
    content = feed_db.select('DATE', f'ID={user_id}')
    today = datetime.now().strftime("%Y-%m-%d")
    if len(content) == 0:
        feed_db.insert('ID, DATE', f"{user_id}, '{today}'")
    else:
        if content[0][0] == today:
            return '今天已经投喂过了'
        feed_db.update(f"DATE = '{today}'", f'ID={user_id}')
    users.love_change(user_id, love_num, False)
    message.text(f'投喂食物成功，获得{love_num}点好感')
    state, msg = users.sign(user_id)
    if state:
        message.text('今天尚未签到，已自动签到：')
        message.text(msg)
    return message


@feed.handle([Cooldown(30, prompt=choice(_lmt_notice)), Depends(is_lk_user)])
async def _(event: Event):
    user_id = event.get_user_id()
    await feed.finish(feed_func(user_id))
