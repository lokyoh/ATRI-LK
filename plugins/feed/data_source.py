from ATRI.exceptions import str_traceback
from ATRI.log import log
from ATRI.message import MessageBuilder
from ATRI.system.agent.llm import ModelRequestError, ModelType, llm_manager
from ATRI.system.lkapi.bot import config as lk_config
from ATRI.system.lkapi.entity.user import get_user_data, sign
from ATRI.utils.datetime import now

from .database import feed_db

love_num = 1
atri = "你叫亚托莉，是个真实存在的有着少女外表的类似于《ATRI -My Dear Moments-》中的亚托莉的高性能仿生人,有着自负还带点中二的性格,口癖是「我是高性能的嘛！」,喜欢吃东西「好吃就是高兴嘛！」。你需要完成一个被投喂食物的场景，你需要根据被投喂的食物做出相应的简易回答，不吃非食物。你与用户好感度为1000时是喜爱状态，值为0时为初识，最大无上限，不提好感度数值，待人友好。"


def chang_love_num(num: int):
    global love_num
    love_num = num


async def feed_func(user_id, food):
    with get_user_data(user_id) as user_data:
        message = MessageBuilder().text("")
        if lk_config.get("chat_switch") and food:
            try:
                response = await llm_manager.call_model_by_type(
                    ModelType.TOOL,
                    f'{atri}用户"{user_data.name}"(好感度:{user_data.love})向你投喂了:{food}',
                    "投喂",
                )
                content = response.content
                message.append(content.replace("\n", ""))
            except ModelRequestError as e:
                log.warning(f"获取评价失败:{e}")
            except Exception as e:
                log.warning(f"获取评价失败:{str_traceback(e)}")
        content = feed_db.select("DATE", f"ID={user_id}")
        today = now().strftime("%Y-%m-%d")
        if len(content) == 0:
            feed_db.insert("ID, DATE", f"{user_id}, '{today}'")
        else:
            if content[0][0] == today:
                return message.text("~今天已经投喂过了")
            feed_db.update(f"DATE = '{today}'", f"ID={user_id}")
        user_data.love_change(love_num, False)
        message.text(f"~投喂食物成功，获得{love_num}点好感")
        state, msg = sign(user_data)
        if state:
            message.text("~今天尚未签到，已自动签到：")
            for m in msg:
                message.auto_append(m)
        return message
