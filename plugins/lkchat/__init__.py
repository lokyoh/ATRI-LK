import os
import re
import string
from random import choice

from nonebot import on_keyword
from nonebot.adapters.onebot.v11 import MessageEvent, Bot
from nonebot.adapters.onebot.v11.event import Event, GroupMessageEvent, PokeNotifyEvent
from nonebot.adapters.onebot.v11.helpers import Cooldown, extract_image_urls
from nonebot.adapters.onebot.v11.message import Message
from nonebot.matcher import Matcher
from nonebot.params import CommandArg, ArgPlainText

from ATRI import TEMP_DIR, RECORD_DIR, IMG_DIR
from ATRI.service import Service
from ATRI.utils import request
from ATRI.utils.img_editor import get_image_bytes
from ATRI.rule import to_bot
from ATRI.system.lkbot.config import config
from ATRI.system.lkbot.util import lk_util
from ATRI.system.help.data_source import Helper
from ATRI.system.lkbot.checker import is_lk_user, is_chat_switch_on
from ATRI.system.lkbot.tools.rec_editor import RECEditor
from ATRI.permission import ADMIN
from ATRI.message import rec_msg, img_msg

from .ai_chat import ai_chat, chat_clear
from .img_chat import get_response

plugin = Service("lk聊天").document("lk插件处理聊天的部分v0.1.0").type(Service.ServiceType.LKPLUGIN).main_cmd("chat")

_lmt_notice = ["慢...慢一..点❤", "冷静1下", "歇会歇会~~", "呜呜...别急", "太快了...受不了", "不要这么快呀"]

tu_chat = plugin.on_command(cmd="图聊", docs="用法:图聊 [可选:文字]\n进行有关图像的一般聊天")


@tu_chat.handle([Cooldown(10, prompt=choice(_lmt_notice))])
async def _(event: Event, matcher: Matcher, args: Message = CommandArg()):
    await is_chat_switch_on(tu_chat)
    await is_lk_user(tu_chat, event)
    text = args.extract_plain_text()
    if text:
        matcher.set_arg("chat_text", args)


@tu_chat.got("chat_text", "没有文字怎么聊？速速")
@tu_chat.got("chat_img", "要聊的图片呢？速速")
async def _(event: MessageEvent, text: str = ArgPlainText("chat_text")):
    img_urls = extract_image_urls(event.message)
    if not img_urls:
        await tu_chat.reject("请发送图片而不是其他东西！！")
    img_paths = []
    for url in img_urls:
        file_name = ''.join(choice(string.ascii_letters + string.digits) for _ in range(5)) + ".jpg"
        file_path = f'{TEMP_DIR}/{file_name}'
        try:
            resp = await request.get(url.replace("https://", "http://"))
            resp.raise_for_status()
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, 'wb') as f:
                f.write(resp.content)
            img_paths.append(file_path)
        except Exception as e:
            await tu_chat.finish(f"怎么办，保存图片失败了捏：{e}")
    response = get_response(img_paths, text)
    await tu_chat.finish(response)


on_talk = plugin.on_message("机器人聊天", "和亚托莉愉快的聊天、交流吧", priority=990, block=False)


@on_talk.handle()
async def _(event: GroupMessageEvent, matcher: Matcher):
    text = event.get_message().extract_plain_text()
    if event.to_me:
        # 语音匹配模块
        async def send_voice(name):
            matcher.stop_propagation()
            res = RECEditor.audio_to_base64(RECORD_DIR / "atri" / f"{name}.mp3")
            await on_talk.send(rec_msg(file=res))
            await on_talk.send(name)

        pattern_dict = {
            r".*萝卜子.*": "萝卜子是对机器人的蔑称！",
            r".*(?:看看你|我看看).*": "不可以看的哦",
            r".*摸*.*[胸屁奶奈熊].*": choice([
                "不要乱摸",
                "这是性骚扰！根据机器人保护法要处以罚款。这下欠款又增加了"
            ]),
            r"不[要行好]?!?$": choice([
                "为什么呢",
                "为什么啊！？"
            ]),
            r"安慰我!?$|我怕怕!?$": "乖......已经没事了",
            r"(?:一起|陪)?睡觉?吧?[!?？]?$": choice([
                "今天一定要一起睡哦！", "可以哦",
                "嗯哼哼！睡吧，就像平时一样安眠吧~",
                "我懂我懂，想抱着我睡觉对吧。真拿你没办法啊~",
                "我无论何时都是Yes", "来吧，来吧，来吧！",
                "真是个小撒娇鬼呢"
            ]),
            r"(?:真是)?太好了!?$": "就是嘛，太好了",
            r"为什么[?？]?$": "我才不管。哼",
            r"你是谁?[\?？]?$": "我是亚托莉（鞠躬）",
            r"早(?:上好|安)?!?$": choice([
                "早上好",
                "早上好.......脸好近呢"
            ]),
            r"来?一?发?火箭拳!?$": "火箭拳————————！！！！",
            r"(?:我要?)?膝枕!?$": "膝枕…...只是膝枕的话，也不是不能给你做......",
        }
        for pattern_item in pattern_dict.keys():
            if re.match(pattern_item, text):
                await send_voice(pattern_dict[pattern_item])
                return
        # 聊天模块
        if not config.config.chat_switch:
            return
        text = lk_util.get_trans_text(event.get_message())
        if text == "":
            await on_talk.send(Helper.service_list())
            return
        sender_id = event.get_user_id()
        if not lk_util.is_valid_user(sender_id):
            await on_talk.send(lk_util.bind_tip)
            return
        matcher.stop_propagation()
        await on_talk.send(await ai_chat(text, sender_id, event.group_id))
    else:
        img_path = IMG_DIR / "atri"
        if re.search(r"好不好|行不行|可以吗|要不要|[行好](?:吗[?？]?|[?？])", text):
            img = choice(["YES.png", choice(["NO.jpg", "NO1.jpg"])])
            await on_talk.finish(img_msg(get_image_bytes(img_path / img)))
        if re.search(r"啊这", text):
            img = "AZ.jpg"
            await on_talk.finish(img_msg(get_image_bytes(img_path / img)))
        if re.search(r"无情", text):
            img = "WQ.jpg"
            await on_talk.finish(img_msg(get_image_bytes(img_path / img)))
        if re.match(r"[?？]+", text):
            img = "WH.jpg"
            await on_talk.finish(img_msg(get_image_bytes(img_path / img)))
        if re.match(r"(?:[干做]得)?漂亮$", text):
            img = choice(["DY.gif", "DY1.gif"])
            await on_talk.finish(img_msg(get_image_bytes(img_path / img)))
        if re.match(r"我?明白了?", text):
            img = "MB.jpg"
            await on_talk.finish(img_msg(get_image_bytes(img_path / img)))
        if re.search(r"吃瓜", text):
            img = "CG.jpg"
            await on_talk.finish(img_msg(get_image_bytes(img_path / img)))
        if re.search(r"加油", text):
            img = "JY.jpg"
            await on_talk.finish(img_msg(get_image_bytes(img_path / img)))
        if re.match(r"不对", text):
            img = "BD.jpg"
            await on_talk.finish(img_msg(get_image_bytes(img_path / img)))
        if re.search(r"看看", text):
            img = "BYK.jpg"
            await on_talk.finish(img_msg(get_image_bytes(img_path / img)))


clear_chat_history = plugin.cmd_as_group(cmd="重置历史", docs="重置AI聊天的聊天历史", permission=ADMIN)


@clear_chat_history.handle()
async def _(event: GroupMessageEvent):
    chat_clear(event.group_id)
    await clear_chat_history.finish(f"全新的{lk_util.bot_name}出现了")


async def get_random_atri(handle):
    if choice([True, False]):
        voice_list = os.listdir(RECORD_DIR / "atri")
        if len(voice_list) == 0:
            return
        voice = choice(voice_list)
        result = RECEditor.audio_to_base64(RECORD_DIR / "atri" / voice)
        await handle.send(rec_msg(file=result))
        await handle.send(re.sub('.mp3', '', voice))
    else:
        img_list = os.listdir(IMG_DIR / "atri")
        if len(img_list) == 0:
            return
        img = choice(img_list)
        await handle.send(img_msg(get_image_bytes(IMG_DIR / "atri" / img)))


poke = plugin.on_notice("戳一戳", "处理戳一戳事件")


@poke.handle()
async def _(event: PokeNotifyEvent, bot: Bot):
    if str(event.target_id) == bot.self_id:
        await get_random_atri(poke)


atri_voice = plugin.on_command(cmd="亚托莉语音", docs="随机亚托莉语音")


@atri_voice.handle()
async def _():
    await get_random_atri(atri_voice)


my_wife = on_keyword({"老婆"}, rule=to_bot(), priority=5, block=False)


@my_wife.handle()
async def _(event: Event, matcher: Matcher):
    if not lk_util.is_master(event.get_user_id()):
        matcher.stop_propagation()
        await my_wife.send(img_msg(get_image_bytes(f'{IMG_DIR}/laopo.jpg')))
