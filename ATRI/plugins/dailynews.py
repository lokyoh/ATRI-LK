import json
import os
from random import choice

import requests
from nonebot import get_bots
from nonebot.adapters.onebot.v11 import MessageSegment, Message
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import GroupMessageEvent
from nonebot.adapters.onebot.v11.helpers import Cooldown

from ATRI import driver
from ATRI.permission import ADMIN
from ATRI.plugins.lkbot.util import PLUGIN_DIR
from ATRI.service import Service
from ATRI.utils.apscheduler import scheduler

plugin = Service("每日新闻").document("订阅每日新闻服务").type(Service.ServiceType.FUNCTION)

url = "http://dwz.2xb.cn/zaob"
_lmt_notice = ["慢...慢一..点❤", "冷静1下", "歇会歇会~~", "呜呜...别急", "太快了...受不了", "不要这么快呀"]
DATA_PATH = f"{PLUGIN_DIR}/news_groups.json"


def save_news_config():
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, 'w', encoding='utf-8') as _file:
        json.dump(news_config, _file, indent=4, ensure_ascii=False)


if os.path.exists(DATA_PATH):
    with open(DATA_PATH, 'r', encoding='utf-8') as file:
        news_config = json.load(file)
else:
    news_config = {"groups": []}
    save_news_config()

today_news = plugin.on_command(cmd='今日新闻', docs="查看今日新闻")


@today_news.handle([Cooldown(60 * 60, prompt=choice(_lmt_notice))])
async def _():
    await today_news.finish(get_news())


news_sub = plugin.on_command(cmd="每日新闻订阅", docs="管理本群的新闻订阅", permission=ADMIN)


@news_sub.handle()
async def _(event: GroupMessageEvent):
    group_id = str(event.group_id)
    if group_id in news_config["groups"]:
        news_config["groups"].remove(group_id)
        save_news_config()
        await news_sub.finish("本群每日新闻订阅已关闭")
    else:
        news_config["groups"].append(group_id)
        save_news_config()
        await news_sub.finish("本群每日新闻订阅已开启")


async def daily_job():
    message = get_news()
    for bot in get_bots().values():
        if type(bot) is Bot:
            group_list = await bot.get_group_list()
            for group in group_list:
                group_id = str(group["group_id"])
                if group_id in news_config["groups"]:
                    await bot.send_group_msg(group_id=group_id, message=Message().append(message))


driver().on_startup(lambda: scheduler.add_job(daily_job, 'cron', hour=7, minute=0))


def get_news() -> MessageSegment:
    response = requests.get(url)
    if response.status_code == 200:
        url_json = response.json()
        image_url = str(url_json["imageUrl"])
        return MessageSegment.image(file=image_url)
    return MessageSegment.text(text="很遗憾，获取今日新闻失败了捏")
