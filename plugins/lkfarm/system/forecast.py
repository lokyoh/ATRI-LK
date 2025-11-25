from random import choice

from nonebot import get_bots
from nonebot.adapters.onebot.v11.bot import Bot

from .farm_user import user_farm_datas
from .. import config
from ..config import LKFarmConfig


def get_weather(weather):
    if weather == 0:
        return '晴'
    elif weather == 1:
        return '雨'
    elif weather == 2:
        return '雷雨'
    elif weather == 3:
        return '雪'
    return f'未知天气{weather}'


def get_next_weather(weather):
    if weather == 0:
        return choice(['明天天气晴朗，风和日丽！', '全天都会风和日丽，万里无云。'])
    elif weather == 1:
        return '明天全天有雨。'
    elif weather == 2:
        return '好像有场风暴正在接近。预计将会有雷电。'
    elif weather == 3:
        return choice(['都多穿点，各位，明天将会下雪！', '明天的降雪可能会达到几英尺。'])
    return f'未知天气{weather}'


async def weather_forecast():
    _config: LKFarmConfig = config.config()
    weather = get_weather(user_farm_datas.weather)
    next_weather = get_next_weather(user_farm_datas.next_weather)
    msg = (f'农场天气预报:\n'
           f'今日:{weather}\n'
           f'明日:{next_weather}')
    for bot in get_bots().values():
        if type(bot) is Bot:
            group_list = await bot.get_group_list()
            for group in group_list:
                group_id = int(group["group_id"])
                if group_id in _config.weather_forecast_group:
                    await bot.send_group_msg(group_id=group_id, message=msg)
