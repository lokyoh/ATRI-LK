import json
import os
import random
import re
import string
from random import choice

from nonebot import on_keyword
from nonebot.adapters.onebot.v11 import MessageEvent, Bot
from nonebot.adapters.onebot.v11.event import Event, GroupMessageEvent, PokeNotifyEvent
from nonebot.adapters.onebot.v11.helpers import Cooldown, extract_image_urls
from nonebot.adapters.onebot.v11.message import Message, MessageSegment
from nonebot.matcher import Matcher
from nonebot.params import CommandArg, ArgPlainText, Arg

from ATRI import TEMP_DIR, RECORD_DIR, IMG_DIR, TEXT_DIR
from ATRI.service import Service
from ATRI.utils import request
from ATRI.utils.img_editor import get_image_bytes
from ATRI.rule import to_bot
from ATRI.plugins.lkbot.config import config
from ATRI.plugins.lkbot.util import lk_util
from ATRI.plugins.help.data_source import Helper
from ATRI.plugins.lkbot.checker import is_lk_user, is_chat_switch_on
from ATRI.plugins.lkbot.system.tools.rec_editor import RECEditor
from ATRI.permission import ADMIN

from .ai_chat import ai_chat, chat_clear
from .img_chat import get_response
from .tiangou import tiangou

plugin_chat = Service("lk聊天").document("l_o_o_k的聊天插件").type(Service.ServiceType.LKPLUGIN)

_lmt_notice = ["慢...慢一..点❤", "冷静1下", "歇会歇会~~", "呜呜...别急", "太快了...受不了", "不要这么快呀"]

tu_chat = plugin_chat.on_command(cmd="图聊", docs="用法:图聊 [可选:文字]\n进行有关图像的一般聊天")


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


on_talk = plugin_chat.on_message("机器人聊天", "和亚托莉愉快的聊天、交流吧", priority=990, block=False)


@on_talk.handle()
async def _(event: GroupMessageEvent, matcher: Matcher):
    text = event.get_message().extract_plain_text()
    if event.to_me:
        # 语音匹配模块
        async def send_voice(name):
            matcher.stop_propagation()
            res = RECEditor.audio_to_base64(RECORD_DIR / "atri" / f"{name}.mp3")
            await on_talk.send(MessageSegment.record(file=res))
            await on_talk.send(name)

        pattern_dict = {
            r".*萝卜子.*": "萝卜子是对机器人的蔑称！",
            r".*(?:看看你|我看看).*": "不可以看的哦",
            r".*摸*.*[胸屁奶奈熊].*": choice(["不要乱摸", "这是性骚扰！根据机器人保护法要处以罚款。这下欠款又增加了"]),
            r"不[要行好]?!?$": choice(["为什么呢", "为什么啊！？"]),
            r"安慰我!?$|我怕怕!?$": "乖......已经没事了",
            r"(?:一起|陪)?睡觉?吧?[!?？]?$": choice(["今天一定要一起睡哦！", "可以哦"
                                                       , "嗯哼哼！睡吧，就像平时一样安眠吧~"
                                                       , "我懂我懂，想抱着我睡觉对吧。真拿你没办法啊~"
                                                       , "我无论何时都是Yes", "来吧，来吧，来吧！"
                                                       , "真是个小撒娇鬼呢"]),
            r"(?:真是)?太好了!?$": "就是嘛，太好了",
            r"为什么[?？]?$": "我才不管。哼",
            r"你是谁?[\?？]?$": "我是亚托莉（鞠躬）",
            r"早(?:上好|安)?!?$": choice(["早上好", "早上好.......脸好近呢"]),
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
        if re.search(r"好不好|行不行|可以吗|要不要|[行好](?:吗[?？]?|[?？])", text):
            img = random.choice(["YES.png", "NO.jpg"])
            await on_talk.finish(MessageSegment.image(get_image_bytes(IMG_DIR / "atri" / img)))
        if re.search(r"啊这", text):
            img = "AZ.jpg"
            await on_talk.finish(MessageSegment.image(get_image_bytes(IMG_DIR / "atri" / img)))
        if re.search(r"无情", text):
            img = "WQ.jpg"
            await on_talk.finish(MessageSegment.image(get_image_bytes(IMG_DIR / "atri" / img)))
        if re.match(r"[?？]+", text):
            img = "WH.jpg"
            await on_talk.finish(MessageSegment.image(get_image_bytes(IMG_DIR / "atri" / img)))


clear_chat_history = plugin_chat.cmd_as_group(cmd="重置历史", docs="重置AI聊天的聊天历史", permission=ADMIN)


@clear_chat_history.handle()
async def _(event: GroupMessageEvent):
    chat_clear(event.group_id)
    await clear_chat_history.finish(f"全新的{lk_util.bot_name}出现了")


plugin = Service("lk功能").document("l_o_o_k的各种功能插件的集合").type(Service.ServiceType.LKPLUGIN)


async def get_random_atri(handle):
    voice_list = os.listdir(RECORD_DIR / "atri")
    if len(voice_list) == 0:
        return
    voice = random.choice(voice_list)
    result = RECEditor.audio_to_base64(RECORD_DIR / "atri" / voice)
    await handle.send(MessageSegment.record(file=result))
    await handle.send(re.sub('.mp3', '', voice))


poke = plugin.on_notice("戳一戳", "处理戳一戳事件")


@poke.handle()
async def _(event: PokeNotifyEvent, bot: Bot):
    if str(event.target_id) == bot.self_id:
        await get_random_atri(poke)


my_wife = on_keyword({"老婆"}, rule=to_bot(), priority=5, block=False)


@my_wife.handle()
async def _(event: Event, matcher: Matcher):
    if not lk_util.is_master(event.get_user_id()):
        matcher.stop_propagation()
        await my_wife.send(MessageSegment.image(get_image_bytes(f'{IMG_DIR}/laopo.jpg')))


dg_voice = plugin.on_keyword({"骂"}, docs="爽！再来一句！(需@bot)", rule=to_bot(), priority=5, block=True)


@dg_voice.handle()
async def _():
    voice_list = os.listdir(RECORD_DIR / "dinggong")
    if len(voice_list) == 0:
        return
    voice = random.choice(os.listdir(RECORD_DIR / "dinggong"))
    result = RECEditor.audio_to_base64(RECORD_DIR / "dinggong" / voice)
    await dg_voice.send(MessageSegment.record(file=result))
    await dg_voice.send(voice.split("_")[1])


get_tiangou = plugin.on_command(cmd='舔狗日记', docs='发送一条舔狗日记')


@get_tiangou.handle()
async def _():
    await get_tiangou.finish(tiangou.get_tiangou())


fa_dian = plugin.on_command("每日发癫", docs="不每天对Ta发癫很难受呀！")


@fa_dian.handle()
async def _(matcher: Matcher, args: Message = CommandArg()):
    if args:
        matcher.set_arg("fa_dian_name", args)


@fa_dian.got("fa_dian_name", prompt="所以你要对谁发癫呢")
async def _(bot: Bot, event: GroupMessageEvent, msg: Message = Arg("fa_dian_name")):
    cost = msg.extract_plain_text()
    for segment in msg:
        if segment.type == 'at':
            qq = segment.data['qq']
            if lk_util.is_valid_user(qq):
                cost = lk_util.get_name(qq)
            else:
                info = await bot.get_group_member_info(group_id=event.group_id, user_id=int(event.get_user_id()))
                cost = info['nickname']
            break
    if cost == '' or cost == ' ':
        await fa_dian.finish("没有检测到对象")
    file_path = TEXT_DIR / "fa_dian.json"
    with open(file_path, "r", encoding="utf-8") as f:
        nami = json.load(f)["post"]
    random_post = random.choice(nami).replace("阿咪", cost)
    await fa_dian.send(random_post)
    await fa_dian.finish("大家快来看看，又有🦐头仁在这发癫了")
