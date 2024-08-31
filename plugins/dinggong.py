import os
import random

from nonebot.adapters.onebot.v11.message import MessageSegment

from ATRI import RECORD_DIR
from ATRI.service import Service
from ATRI.system.lkbot.tools.rec_editor import RECEditor

ding_gong = Service("钉宫语录").document("骂我 发送一条钉宫语录").type(Service.ServiceType.ENTERTAINMENT)

dg_voice = ding_gong.on_command("骂我", docs="爽！再来一句！")


@dg_voice.handle()
async def _():
    voice_list = os.listdir(RECORD_DIR / "dinggong")
    if len(voice_list) == 0:
        return
    voice = random.choice(os.listdir(RECORD_DIR / "dinggong"))
    result = RECEditor.audio_to_base64(RECORD_DIR / "dinggong" / voice)
    await dg_voice.send(MessageSegment.record(file=result))
    await dg_voice.send(voice.split("_")[1])
