import json
import random

from nonebot.matcher import Matcher
from nonebot.params import CommandArg, Arg
from nonebot.adapters.onebot.v11.message import Message
from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11.event import GroupMessageEvent

from ATRI import TEXT_DIR
from ATRI.service import Service
from ATRI.system.lkbot import lk_util

daily_fa_dian = Service("每日发癫").document("不每天对Ta发癫很难受呀！").type(Service.ServiceType.ENTERTAINMENT)

fa_dian = daily_fa_dian.on_command("每日发癫", docs="不每天对Ta发癫很难受呀！")


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
