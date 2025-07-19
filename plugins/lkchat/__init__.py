import os
import random
import re
import string
from random import choice

from nonebot import on_keyword
from nonebot.adapters.onebot.v11 import MessageEvent, Bot, ActionFailed
from nonebot.adapters.onebot.v11.event import Event, GroupMessageEvent, PokeNotifyEvent
from nonebot.adapters.onebot.v11.helpers import Cooldown, extract_image_urls
from nonebot.adapters.onebot.v11.message import Message
from nonebot.matcher import Matcher
from nonebot.params import CommandArg, ArgPlainText, Depends

from ATRI import TEMP_DIR, RECORD_DIR, IMG_DIR
from ATRI.service import Service
from ATRI.log import log
from ATRI.utils import request
from ATRI.utils.img_editor import get_image_bytes
from ATRI.rule import to_bot
from ATRI.system.lkapi.bot.config import configs
from ATRI.system.lkapi.bot import util as lk_util
from ATRI.system.lkapi.bot.checker import is_lk_user, is_chat_switch_on
from ATRI.system.lkapi.utils.audio import AudioEditor
from ATRI.system.help.data_source import Helper
from ATRI.permission import ADMIN
from ATRI.message import rec_msg, img_msg

from .ai_chat import ai_chat, chat_clear
from .img_chat import get_response

plugin = Service(
    "lk聊天",
    "lk插件处理聊天的部分",
    "0.3.2",
    Service.ServiceType.LKPLUGIN
).main_cmd("chat")

_lmt_notice = ["慢...慢一..点❤", "冷静1下", "歇会歇会~~", "呜呜...别急", "太快了...受不了", "不要这么快呀"]

tu_chat = plugin.on_command(cmd="图聊", docs="用法:图聊 [可选:文字]\n进行有关图像的一般聊天")


@tu_chat.handle([Cooldown(10, prompt=choice(_lmt_notice)), Depends(is_lk_user), Depends(is_chat_switch_on)])
async def _(matcher: Matcher, args: Message = CommandArg()):
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
            res = AudioEditor.audio_to_base64(RECORD_DIR / "atri" / f"{name}.mp3")
            await on_talk.send(rec_msg(file=res))
            await on_talk.send(name)

        pattern_dict = {
            r".*萝卜子.*": "萝卜子是对机器人的蔑称！",
            r".*(?:看看你|我看看).*": "不可以看的哦",
            r".*摸+.*[胸屁奶奈乃熊bB].*": choice([
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
        if not configs.chat_switch:
            return
        text = lk_util.get_trans_text(event.get_message())
        if text == "":
            try:
                await on_talk.send(Helper().get_service_list())
            except ActionFailed:
                await on_talk.send(Helper().get_text_list())
            return
        sender_id = event.get_user_id()
        if not lk_util.is_valid_user(sender_id):
            await on_talk.send(lk_util.bind_tip)
            return
        matcher.stop_propagation()
        await on_talk.send(await ai_chat(text, sender_id, event.group_id))
    else:
        img_path = IMG_DIR / "atri"
        pattern_img_map = [
            (r"好不好|行不行|可以吗|要不要|[行好](?:吗[?？]?|[?？])",
             lambda: choice(["YES.png", choice(["NO.jpg", "NO1.jpg"])])),
            (r"啊这", "AZ.jpg"),
            (r"无情", "WQ.jpg"),
            (r"^[?？]+$", "WH.jpg"),
            (r"^(?:[干做]得)?漂亮$", lambda: choice(["DY.gif", "DY1.gif"])),
            (r"我?明白了?", "MB.jpg"),
            (r"吃瓜", "CG.jpg"),
            (r"加油", "JY.jpg"),
            (r"^不对", "BD.jpg"),
            (r"看看你|我看看", "BYK.jpg"),
        ]
        for pattern, img in pattern_img_map:
            if re.search(pattern, text):
                selected_img = img() if callable(img) else img
                await on_talk.finish(img_msg(get_image_bytes(img_path / selected_img)))


clear_chat_history = plugin.cmd_as_group(cmd="重置历史", docs="重置AI聊天的聊天历史", permission=ADMIN)


@clear_chat_history.handle()
async def _(event: GroupMessageEvent):
    chat_clear(event.group_id)
    await clear_chat_history.finish(f"全新的{lk_util.bot_name}出现了")


async def get_random_atri(handle):
    voice_list = os.listdir(RECORD_DIR / "atri")
    if len(voice_list) == 0:
        return
    voice = choice(voice_list)
    result = AudioEditor.audio_to_base64(RECORD_DIR / "atri" / voice)
    await handle.send(rec_msg(file=result))
    await handle.send(re.sub('.mp3', '', voice))


poke = plugin.on_notice("戳一戳", "处理戳一戳事件")

REPLY_MESSAGE = [
    "lsp你再戳？",
    "连个可爱美少女都要戳的肥宅真恶心啊。",
    "你再戳！",
    "？再戳试试？",
    "别戳了别戳了再戳就坏了555",
    f"{lk_util.bot_name}爪巴爪巴，球球别再戳了",
    "你戳你🐎呢？！",
    "那...那里...那里不能戳...绝对...",
    "(。´・ω・)ん?",
    f"有事恁叫{lk_util.bot_name}，别天天一个劲戳戳戳！",
    "欸很烦欸！你戳🔨呢",
    "?",
    "再戳一下试试？",
    "???",
    "正在关闭对您的所有服务...关闭成功",
    "啊呜，太舒服刚刚竟然睡着了。什么事？",
    "正在定位您的真实地址...定位成功。轰炸机已起飞",
    f"别戳了，别戳了，{lk_util.bot_name}的呆毛要掉拉！",
    f"{lk_util.bot_name}在呢！",
    f"你是来找{lk_util.bot_name}玩的嘛？",
    f"别急呀, {lk_util.bot_name}要宕机了!QAQ",
    "你好！Ov<",
    "别戳了，怕疼QwQ",
    f"再戳，{lk_util.bot_name}就要咬你了嗷~",
    "恶龙咆哮，嗷呜~",
    "生气(╯▔皿▔)╯",
    "不要这样子啦（*/ w \\*）",
    "戳坏了",
    "戳坏了，赔钱！",
    f"喂，110吗，有人老戳{lk_util.bot_name}",
    f"别戳{lk_util.bot_name}啦，您歇会吧~",
    f"喂(#`O′) 戳{lk_util.bot_name}干嘛！",
]

@poke.handle()
async def _(event: PokeNotifyEvent, bot: Bot):
    if str(event.target_id) == bot.self_id:
        rand = random.random()
        if rand < 0.25:
            await get_random_atri(poke)
        elif rand < 0.50:
            img_list = os.listdir(IMG_DIR / "atri")
            if len(img_list) == 0:
                return
            img = choice(img_list)
            await poke.send(img_msg(get_image_bytes(IMG_DIR / "atri" / img)))
        elif rand < 0.75:
            await poke.send(choice(REPLY_MESSAGE), at_sender=True)
        else:
            try:
                if event.group_id:
                    await bot.call_api("group_poke", user_id=event.user_id, group_id=event.group_id)
                else:
                    await bot.call_api("friend_poke", user_id=event.user_id)
            except Exception:
                log.warning("戳一戳发送失败")


atri_voice = plugin.on_command(cmd="/亚托莉语音", docs="随机亚托莉语音")


@atri_voice.handle()
async def _():
    await get_random_atri(atri_voice)


my_wife = on_keyword({"老婆"}, rule=to_bot(), priority=5, block=False)


@my_wife.handle()
async def _(event: Event, matcher: Matcher):
    if not lk_util.is_master(event.get_user_id()):
        matcher.stop_propagation()
        await my_wife.send(img_msg(get_image_bytes(f'{IMG_DIR}/laopo.jpg')))
