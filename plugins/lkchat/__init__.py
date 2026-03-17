import os
import random
import re
from random import choice

from nonebot.adapters.onebot.v11 import Bot, Message
from nonebot.adapters.onebot.v11.event import GroupMessageEvent, PokeNotifyEvent
from nonebot.internal.params import ArgPlainText
from nonebot.matcher import Matcher

from ATRI import IMG_DIR
from ATRI.bot import BotUtils
from ATRI.exceptions import str_traceback
from ATRI.log import log
from ATRI.message import MessageBuilder, img_msg, img_msg_from_path, rec_msg
from ATRI.permission import MASTER
from ATRI.service import Service
from ATRI.system.htmlrender import md_to_pic
from ATRI.system.lkapi.ai import chat_manager
from ATRI.system.lkapi.bot import util as lk_util
from ATRI.system.lkapi.bot.config import configs
from ATRI.system.lkapi.utils.audio import AudioEditor

from .config import LKChatConfig

plugin = Service(
    "聊天", "ATRI进行聊天处理的插件", "0.7.2", Service.ServiceType.ENTERTAINMENT
).main_cmd("聊天")
config: LKChatConfig = plugin.add_plugin_config(LKChatConfig).config()

from .chat import chat_model  # noqa: E402
from .data_source import (  # noqa: E402
    REPLY_MESSAGE,
    PreChatEvent,
    get_atri_memery,
    get_random_atri,
    match_atri_img,
    match_atri_voice,
    pre_chat_event,
)
from .explanations import add_word  # noqa: E402
from .user import get_user_info, save_user_info  # noqa: E402

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
    await pre_chat_event.notify(PreChatEvent(matcher, event))
    text = event.get_message().extract_plain_text()
    if event.to_me:
        # 聊天模块
        if not configs.chat_switch:
            voice = match_atri_voice(text)
            if voice:
                await on_talk.send(voice[0])
                await on_talk.finish(voice[1])
            return
        text = lk_util.get_trans_text(event.get_message())
        if text == "":
            await on_talk.finish(
                f'找我有什么事么?如果要查询我有什么功能的话就输入"{BotUtils.get_command_start()}服务列表"吧,高性能的我随时为你服务哦。',
                at_sender=True,
            )
        if len(text) > 100:
            await on_talk.finish("消息长度过长！", at_sender=True)
        sender_id = event.get_user_id()
        if not lk_util.is_valid_user(sender_id):
            await on_talk.finish(lk_util.bind_tip)
        matcher.stop_propagation()
        match_result = re.compile(r"语音(.+)").match(text)
        group_id = event.group_id
        if match_result:
            text = match_result.group(1)
        else:
            voice = match_atri_voice(text)
            if voice:
                await chat_model.add_history(group_id, sender_id, text)
                await on_talk.send(voice[0])
                await on_talk.finish(voice[1])
        await chat_model.add_history(group_id, sender_id, text)
        try:
            response = await chat_model.get_resp(bot, group_id, sender_id)
        except Exception as e:
            log.warning(str_traceback(e))
            await on_talk.finish(f"真是的，{lk_util.bot_name}被玩坏了，呜呜呜...")
        if match_result:
            record_file = AudioEditor.get_tts_file(response[0])
            await on_talk.finish(
                rec_msg(file=AudioEditor().audio_to_base64(record_file))
            )
        else:
            if "\n\n" in response[0]:
                await on_talk.finish(img_msg(await md_to_pic(response[0])))
            msg = Message()
            for m in response:
                msg.append(m)
            await on_talk.finish(msg, at_sender=True)
    else:
        if configs.chat_switch:
            group_id = event.group_id
            sender_id = event.get_user_id()
            t_text = lk_util.get_trans_text(event.get_message())
            await chat_model.add_history(group_id, sender_id, t_text)
        img = match_atri_img(text)
        if img:
            await on_talk.finish(img)


change_model = plugin.cmd_as_group(
    "切换模型", "切换机器人聊天所使用的语言模型默认为`gemini-main`", permission=MASTER
)


@change_model.got(
    "chat_model",
    f"请输入要选择的类型名:\n{'\n'.join(f'{i}.{_type}' for i, _type in enumerate(chat_manager.get_chats_name(), 1))}",
)
async def _(arg: str = ArgPlainText("chat_model")):
    if arg in chat_manager.get_chats_name():
        config.help_type = arg
        plugin.plugin_config().change_config(config)
    else:
        await change_model.finish("请输入正确的类型")
    await change_model.finish("切换成功")


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
    await del_mem.finish(msg)


poke = plugin.on_notice("戳一戳", "处理戳一戳事件")


@poke.handle()
async def _(event: PokeNotifyEvent, bot: Bot):
    if str(event.target_id) == bot.self_id:
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


atri_voice = plugin.on_command(cmd="亚托莉语音", docs="随机亚托莉语音")


@atri_voice.handle()
async def _():
    a_v = get_random_atri()
    if a_v:
        await atri_voice.send(a_v[0])
        await atri_voice.finish(a_v[1])
