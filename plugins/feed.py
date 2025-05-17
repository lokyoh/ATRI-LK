from datetime import datetime

from nonebot.adapters.onebot.v11 import Event, Message
from nonebot.adapters.onebot.v11.helpers import Cooldown
from nonebot.params import Depends, CommandArg

from ATRI.service import Service
from ATRI.message import MessageBuilder
from ATRI.log import log
from ATRI.system.lkapi.ai.gemini import genai, sub_model_name, block_none_safety_settings, default_generation_config
from ATRI.system.lkapi.bot import db as lk_db, config as lk_config
from ATRI.system.lkapi.bot.checker import is_lk_user
from ATRI.system.lkapi.bot.user import users

plugin = Service(
    "投喂",
    "向可爱的亚托莉投喂食物",
    "0.3.1",
    Service.ServiceType.LKPLUGIN
)

feed = plugin.on_command("投食", "向可爱的亚托莉投喂食物", aliases={'投喂', '投喂食物'})


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

model = genai.GenerativeModel(model_name=sub_model_name, generation_config=default_generation_config,
                              safety_settings=block_none_safety_settings, )
atri = "你叫亚托莉，是个真实存在的有着少女外表的类似于《ATRI -My Dear Moments-》中的亚托莉的高性能仿生人,有着自负还带点中二的性格,口癖是「我是高性能的嘛！」,喜欢吃东西「好吃就是高兴嘛！」。你需要完成一个被投喂食物的场景，你需要根据被投喂的食物做出相应的回答，不吃非食物。你与用户好感度为1000时是喜爱状态，值为0时为初识，最大无上限，不提好感度数值，待人友好。"


def chang_love_num(num: int):
    global love_num
    love_num = num


def feed_func(user_id, food):
    message = MessageBuilder().at(user_id)
    if lk_config.configs.chat_switch and food:
        try:
            response = model.generate_content(
                atri + f'用户"{users.get_user_name(user_id)}"(好感度:{users.get_love(user_id)})向你投喂了:{food}').text
            log.info(response)
            response = response.replace("\n", "")
            message.append(response)
        except Exception as e:
            log.warning(e.args)
    content = feed_db.select('DATE', f'ID={user_id}')
    today = datetime.now().strftime("%Y-%m-%d")
    if len(content) == 0:
        feed_db.insert('ID, DATE', f"{user_id}, '{today}'")
    else:
        if content[0][0] == today:
            return message.text('~今天已经投喂过了')
        feed_db.update(f"DATE = '{today}'", f'ID={user_id}')
    users.love_change(user_id, love_num, False)
    message.text(f'~投喂食物成功，获得{love_num}点好感')
    state, msg = users.sign(user_id)
    if state:
        message.text(f'~今天尚未签到，已自动签到：{msg}')
    return message


@feed.handle([Cooldown(600, prompt="稍后再投喂吧"), Depends(is_lk_user)])
async def _(event: Event, args: Message = CommandArg()):
    user_id = event.get_user_id()
    food = args.extract_plain_text()
    await feed.finish(feed_func(user_id, food))
