import inspect
from datetime import datetime
from random import choice
import os
import re

from nonebot.adapters.onebot.v11 import MessageSegment, GroupMessageEvent
from nonebot.exception import FinishedException

from ATRI import IMG_DIR, RECORD_DIR
from ATRI.message import img_msg, rec_msg
from ATRI.utils.img_editor import get_image_bytes
from ATRI.utils.event import DictEvent
from ATRI.exceptions import str_traceback, EventRuntimeError
from ATRI.log import log
from ATRI.system.lkapi.utils.audio import AudioEditor
from ATRI.system.lkapi.bot import util as lk_util

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
VOICE_PATTERN = {
    r".*萝卜子.*": [
        "萝卜子是对机器人的蔑称！.opus",
        "啊，不准说这个词！.opus"
    ],
    r".*(?:看看你|我看看).*": [
        "摆……摆出这幅表情也没有用。不给你看，很害羞的.opus",
        "不可以看.opus",
        "不可以看的哦.opus",
        "真是的～，不都说了不可以看了么。我要根据机器人保护法对你进行铁拳制裁！.opus"
    ],
    r".*摸+.*[胸屁奶奈乃熊bB逼].*": [
        "啊呜呜，不要来回来去地摸～～.opus",
        "这是性骚扰！根据机器人保护法要处以罚款。这下欠款又增加了.opus"
    ],
    r".*摸+.*[头脸].*": ["啊呜呜，不要来回来去地摸～～.opus"],
    r"不[要行好]?!?$": [
        "为什么呢？.opus",
        "为什么啊！？.opus",
        "？　为什么呢？.opus"
    ],
    r"安慰我!?$|我怕怕!?$": [
        "乖……已经没事了.opus",
        "乖乖乖.opus"
    ],
    r"(?:一起|陪)?睡觉?吧?[!?？]?$": [
        "今天一定要一起睡哦！.opus",
        "嗯哼哼～，睡吧♪ 就像平常一样安眠吧.opus",
        "我懂我懂，想抱着我睡觉对吧。真拿你没办法呀～.opus",
        "我无论何时都是YES！.opus",
        "来吧，来吧，来吧！！.opus",
        "真是个小撒娇鬼呢.opus"
    ],
    r"(?:真是)?太好了!?$": ["太好了呢.opus"],
    r"为什么[?？]?$": ["我才不管。哼.opus"],
    r"你是谁?[\?？]?$": ["我叫亚托莉。（鞠躬）.opus"],
    r"早(?:上好|安)?!?$": [
        "早上好.opus",
        "早上好……脸好近呢.opus"
    ],
    r"来?一?发?火箭拳!?$": ["火箭拳——————————！！！！.opus"],
    r"(?:我要?)?膝枕!?$": ["膝枕……只是膝枕的话，也不是不能给你做…….opus"],
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
    (r"看看你|我看看", "BYK.jpg"),
]


class PreChatEvent(DictEvent):
    async def notify(self, matcher, event) -> bool:
        """触发该事件"""
        for key in self.listeners:
            try:
                func = self.listeners[key]
                if inspect.iscoroutinefunction(func):
                    stop, resp = await func(event=event)
                else:
                    stop, resp = func(event=event)
                if resp:
                    await matcher.send(resp)
                if stop:
                    matcher.stop_propagation()
                    return True
            except FinishedException as e:
                raise e from e
            except Exception as e:
                str_tb = str_traceback(e)
                log.error(str_tb)
                raise EventRuntimeError(f"事件{self.name}在执行{key}时出现错误", str_tb)
        return False


pre_chat_event = PreChatEvent("pre_chat")


def get_random_atri() -> tuple[MessageSegment, str] | None:
    voice_list = os.listdir(RECORD_DIR / "atri")
    if len(voice_list) == 0:
        return None
    voice = choice(voice_list)
    result = AudioEditor.audio_to_base64(RECORD_DIR / "atri" / voice)
    return rec_msg(file=result), re.sub('.mp3', '', voice)


@pre_chat_event.handle("atri_birthday")
def on_birthday(event: GroupMessageEvent) -> tuple[bool, str | None]:
    text = event.get_plaintext()
    date = datetime.now()
    if date.month == 8 and date.day == 28:
        a_b_p = ["生日", "生快", "birth", "Birth"]
        for a_b in a_b_p:
            if a_b in text:
                return True, choice(
                    ["哇~谢谢你。鞠躬", "啊、多谢", img_msg(get_image_bytes(IMG_DIR / "atri_" / "SR.gif"))])
    return False, None


def get_atri_memery(mem):
    md_text = "# 亚托莉对你的记忆\n\n"
    md_text += "\n".join(f'- {i}:{item}' for i, item in enumerate(mem, 1))
    md_text += "\n\n> 输入`chat.删除记忆 [标号]`来删除指定记忆,[标号]为数字,例如:`chat.删除记忆 1`"
    return md_text
