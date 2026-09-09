import os
import random
from random import choice

from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11.event import GroupMessageEvent, PokeNotifyEvent
from nonebot.matcher import Matcher

from ATRI import IMG_DIR
from ATRI.log import log
from ATRI.message import MessageBuilder, img_msg, img_msg_from_path
from ATRI.permission import MASTER
from ATRI.service import Service
from ATRI.system.agent.agent.explanations import add_word
from ATRI.system.agent.agent.user import get_user_info, save_user_info
from ATRI.system.htmlrender import md_to_pic

from .config import LKChatConfig

plugin = Service(
    "聊天", "ATRI进行聊天处理的插件", "0.8.1", Service.ServiceType.ENTERTAINMENT
).main_cmd("聊天")
config: LKChatConfig = plugin.add_plugin_config(LKChatConfig).config()

from .data_source import (
    REPLY_MESSAGE,
    PreChatEvent,
    call_agent,
    get_atri_memery,
    get_random_atri,
    match_atri_img,
    match_atri_voice,
    pre_chat_event,
)

_lmt_notice = [
    "慢...慢一..点❤",
    "冷静1下",
    "歇会歇会~~",
    "呜呜...别急",
    "太快了...受不了",
    "不要这么快呀",
]

on_talk = plugin.on_message(
    "机器人聊天", "和亚托莉愉快的聊天、交流吧", priority=990, block=False
)


@on_talk.handle()
async def _(event: GroupMessageEvent, matcher: Matcher, bot: Bot):
    group_id = str(event.group_id)
    if not config.chat_switch:
        await pre_chat_event.notify(PreChatEvent(matcher, event))
        text = event.get_message().extract_plain_text()
        if event.to_me:
            voice = match_atri_voice(text)
            if voice:
                await on_talk.send(voice[0])
                await on_talk.finish(voice[1])
        else:
            img = match_atri_img(text)
            if img:
                await on_talk.finish(img)
    elif group_id in config.whit_list:
        await call_agent(event, matcher, bot)


word_add = plugin.cmd_as_group(
    "添加解释",
    "为词语添加解释，用法：chat.添加解释 词语 解释 重要度(0-100越大越重要)",
    permission=MASTER,
)


@word_add.handle()
async def _(event: GroupMessageEvent):
    k = event.get_plaintext().split(" ")
    if len(k) != 4:
        await word_add.finish("格式错误")
    try:
        add_word(k[1], k[2], int(k[3]))
    except Exception:
        await word_add.finish("输入错误")
    await word_add.finish("添加成功")


show_mem = plugin.cmd_as_group("查看记忆", "查看亚托莉对你的记忆")


@show_mem.handle()
async def _(event: GroupMessageEvent):
    if event:
        user_info = get_user_info(int(event.get_user_id()))
        mem = user_info.memery
        if len(mem) == 0:
            await show_mem.finish("暂时没有对你的记忆哦，快去与亚托莉多多交流吧")
        await show_mem.finish(img_msg(await md_to_pic(get_atri_memery(mem))))


del_mem = plugin.cmd_as_group("删除记忆", "删除亚托莉对你的记忆")


@del_mem.handle()
async def _(event: GroupMessageEvent):
    k = event.get_plaintext().split(" ")
    if len(k) != 2:
        await del_mem.finish("格式错误")
    try:
        user_id = int(event.get_user_id())
        user_info = get_user_info(user_id)
        num = int(k[1])
        user_info.memery.pop(num - 1)
        save_user_info(user_id, user_info)
        msg = MessageBuilder().text("删除成功")
        if num - 1 > 0:
            msg.image(await md_to_pic(get_atri_memery(user_info.memery)))
    except Exception:
        await del_mem.finish("输入错误")
        return
    await del_mem.finish(msg)


poke = plugin.on_notice("戳一戳", "处理戳一戳事件")


@poke.handle()
async def _(event: PokeNotifyEvent, bot: Bot, matcher: Matcher):
    if event.is_tome():
        rand = random.random()
        if rand < 0.10:
            a_v = get_random_atri()
            if a_v:
                await poke.send(a_v[0])
                await poke.send(a_v[1])
        elif rand < 0.30:
            img_list = os.listdir(IMG_DIR / "atri")
            if len(img_list) == 0:
                return
            img = choice(img_list)
            await poke.send(img_msg_from_path(IMG_DIR / "atri" / img))
        elif rand < 0.70:
            await poke.send(choice(REPLY_MESSAGE), at_sender=True)
        else:
            try:
                if event.group_id:
                    await bot.call_api(
                        "group_poke", user_id=event.user_id, group_id=event.group_id
                    )
                else:
                    await bot.call_api("friend_poke", user_id=event.user_id)
            except Exception:
                log.warning("戳一戳发送失败")
        matcher.stop_propagation()


atri_voice = plugin.on_command(cmd="亚托莉语音", docs="随机亚托莉语音")


@atri_voice.handle()
async def _():
    a_v = get_random_atri()
    if a_v:
        await atri_voice.send(a_v[0])
        await atri_voice.finish(a_v[1])
