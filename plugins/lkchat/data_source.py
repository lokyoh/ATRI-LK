from datetime import datetime
from pathlib import Path
from random import choice
import os
import re

from nonebot.adapters.onebot.v11 import MessageSegment, GroupMessageEvent, Bot, Message
from nonebot.exception import FinishedException
from nonebot.matcher import Matcher

from ATRI import IMG_DIR, RECORD_DIR
from ATRI.exceptions import str_traceback
from ATRI.log import log
from ATRI.message import img_msg_from_path, rec_msg_from_path
from ATRI.system.agent.agent import ATRIAgent
from ATRI.system.agent.agent.sender import QQChatSender
from ATRI.system.lkapi.bot import util as lk_util
from ATRI.utils.event import AsyncBaseEvents, BaseEvent

from . import config

REPLY_MESSAGE = [
    "lsp你再戳？",
    "连个可爱美少女都要戳的肥宅真恶心啊。",
    "你再戳！",
    "？再戳试试？",
    "别戳了别戳了再戳就坏了555",
    "我爪巴爪巴，球球别再戳了",
    "你戳你🐎呢？！",
    "那...那里...那里不能戳...绝对...",
    "(。´・ω・)ん?",
    "有事恁叫我，别天天一个劲戳戳戳！",
    "欸很烦欸！你戳🔨呢",
    "?",
    "再戳一下试试？",
    "???",
    "正在关闭对您的所有服务...关闭成功",
    "啊呜，太舒服刚刚竟然睡着了。什么事？",
    "正在定位您的真实地址...定位成功。轰炸机已起飞",
    "别戳了，别戳了，我的呆毛要掉拉！",
    "我在呢！",
    "你是来找我玩的嘛？",
    "别急呀, 我要宕机了!QAQ",
    "你好！Ov<",
    "别戳了，怕疼QwQ",
    "再戳，我就要咬你了嗷~",
    "恶龙咆哮，嗷呜~",
    "生气(╯▔皿▔)╯",
    "不要这样子啦（*/ w \\*）",
    "戳坏了",
    "戳坏了，赔钱！",
    "喂，110吗，有人老戳我",
    "别戳我啦，您歇会吧~",
    "喂(#`O′) 戳我干嘛！",
]
VOICE_PATTERN = {
    r".*萝卜子.*": [
        "萝卜子是对机器人的蔑称！.mp3",
        "啊，不准说这个词！.mp3"
    ],
    r".*(?:看看你|我看看).*": [
        "摆……摆出这幅表情也没有用。不给你看，很害羞的.mp3",
        "不可以看.mp3",
        "不可以看的哦.mp3",
        "真是的～，不都说了不可以看了么。我要根据机器人保护法对你进行铁拳制裁！.mp3"
    ],
    r".*摸+.*[胸屁奶奈乃熊bB逼].*": [
        "啊呜呜，不要来回来去地摸～～.mp3",
        "这是性骚扰！根据机器人保护法要处以罚款。这下欠款又增加了.mp3"
    ],
    r".*摸+.*[头脸].*": ["啊呜呜，不要来回来去地摸～～.mp3"],
    r"不[要行好]?!?$": [
        "为什么呢？.mp3",
        "为什么啊！？.mp3",
        "？　为什么呢？.mp3"
    ],
    r"安慰我!?$|我怕怕!?$": [
        "乖……已经没事了.mp3",
        "乖乖乖.mp3"
    ],
    r"(?:一起|陪)?睡觉?吧?[!?？]?$": [
        "今天一定要一起睡哦！.mp3",
        "嗯哼哼～，睡吧♪ 就像平常一样安眠吧.mp3",
        "我懂我懂，想抱着我睡觉对吧。真拿你没办法呀～.mp3",
        "我无论何时都是YES！.mp3",
        "来吧，来吧，来吧！！.mp3",
        "真是个小撒娇鬼呢.mp3"
    ],
    r"(?:真是)?太好了!?$": ["太好了呢.mp3"],
    r"为什么[?？]?$": ["我才不管。哼.mp3"],
    r"你是谁?[\?？]?$": ["我叫亚托莉。（鞠躬）.mp3"],
    r"早(?:上好|安)?!?$": [
        "早上好.mp3",
        "早上好……脸好近呢.mp3"
    ],
    r"来?一?发?火箭拳!?$": ["火箭拳——————————！！！！.mp3"],
    r"(?:我要?)?膝枕!?$": ["膝枕……只是膝枕的话，也不是不能给你做…….mp3"],
}
IMG_PATTERN = [
    (r"好不好|行不行|可以吗|要不要|[行好](?:吗[?？]?|[?？])",
     lambda: choice(["YES.png", choice(["NO.jpg", "NO1.jpg"])])),
    (r"啊这", "AZ.jpg"),
    (r"无情", "WQ.jpg"),
    (r"^[?？]+$", lambda: choice(["WH.jpg", None])),
    (r"^(?:[干做]得)?漂亮$", lambda: choice(["DY.gif", "DY1.gif"])),
    (r"我?明白了?", "MB.jpg"),
    (r"吃瓜", "CG.jpg"),
    (r"加油", "JY.jpg"),
    (r"^不对", "BD.jpg"),
    (r"^((可以)|能)?让?我?看(看|(一下))你?的?吗?$", "BYK.jpg"),
]


class PreChatEvent(BaseEvent):
    def __init__(self, matcher: Matcher, event: GroupMessageEvent):
        super().__init__('聊天预处理事件')
        self.matcher: Matcher = matcher
        self.message_event: GroupMessageEvent = event


pre_chat_event = AsyncBaseEvents()
"""聊天预处理事件,在处理聊天信息前触发"""


def get_random_atri() -> tuple[MessageSegment, str] | None:
    voice_list = os.listdir(RECORD_DIR / "atri")
    if len(voice_list) == 0:
        return None
    voice = choice(voice_list)
    return rec_msg_from_path(RECORD_DIR / "atri" / voice), Path(voice).stem


@pre_chat_event.handle(1)
async def on_birthday(event: PreChatEvent):
    text = event.message_event.get_plaintext()
    date = datetime.now()
    if date.month == 8 and date.day == 28:
        a_b_p = ["生日", "生快", "birth", "Birth"]
        for a_b in a_b_p:
            if a_b in text:
                await event.matcher.finish(
                    choice(["哇~谢谢你。鞠躬", "啊、多谢", img_msg_from_path(IMG_DIR / "atri_" / "SR.gif")]))


def get_atri_memery(mem):
    md_text = "# 亚托莉对你的记忆\n\n"
    md_text += "\n".join(f'- {i}:{item}' for i, item in enumerate(mem, 1))
    md_text += "\n\n> 输入`/聊天.删除记忆 [标号]`来删除指定记忆,[标号]为数字,例如:`/聊天.删除记忆 1`"
    return md_text


def match_atri_voice(text):
    for pattern_item in VOICE_PATTERN.keys():
        if re.match(pattern_item, text):
            file = choice(VOICE_PATTERN[pattern_item])
            return rec_msg_from_path(RECORD_DIR / "atri" / file), Path(file).stem
    return None


def match_atri_img(text):
    img_path = IMG_DIR / "atri"
    for pattern, img in IMG_PATTERN:
        if re.search(pattern, text):
            selected_img = img() if callable(img) else img
            if selected_img:
                return img_msg_from_path(img_path / selected_img)
    return None


def has_key_word(text):
    return any(key_word in text for key_word in config.key_word)


async def call_agent(event: GroupMessageEvent, matcher: Matcher, bot: Bot):
    group_id = str(event.group_id)
    sender_id = event.get_user_id()
    message = event.get_message()
    skip_chat = False if config.proactively_chat else True
    skip_judgment = False if config.proactively_chat else True
    if event.to_me or has_key_word(message.extract_plain_text()):
        skip_chat = False
        skip_judgment = True
    try:
        sender = QQChatSender(matcher)
        await ATRIAgent.chat(bot, sender, group_id, sender_id, message, skip_chat, skip_judgment)
    except FinishedException:
        raise
    except Exception as e:
        log.warning(str_traceback(e))
        if skip_judgment and not skip_chat:
            await matcher.finish(f"真是的，{lk_util.bot_name}被玩坏了，呜呜呜...")
