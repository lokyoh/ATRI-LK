import re

from nonebot.adapters.onebot.v11 import MessageSegment

from ATRI.log import log
from ATRI.utils.limiter import RateLimiter
from ATRI.system.htmlrender import text_to_pic
from ATRI.system.lkapi.bot import util as lk_util
from ATRI.system.lkapi.utils.audio import AudioEditor
from ATRI.exceptions import str_traceback

from .chat import chat_model

chat_limiter = RateLimiter(15, 60)


async def ai_chat(text, sender_id, group_id, bot):
    if not chat_limiter.is_allowed():
        return "歇会歇会~~"
    match_result = re.compile(r'语音(.+)').match(text)
    if match_result:
        text = match_result.group(1)
    try:
        await chat_model.add_history(group_id, sender_id, text)
        response = await chat_model.get_resp(bot, group_id, sender_id)
    except Exception as e:
        log.warning(str_traceback(e))
        return f"真是的，{lk_util.bot_name}被玩坏了，呜呜呜..."
    if match_result:
        record_file = AudioEditor.get_tts_file(response)
        return MessageSegment.record(file=AudioEditor().audio_to_base64(record_file))
    else:
        if len(response) < 1000:
            return response
        return MessageSegment.image(await text_to_pic(response))
