from ATRI.system.lkapi.ai import chat_manager
from ATRI.log import log
from ATRI.utils.limiter import RateLimiter

from .atri import ATRI
from .history import ChatHistory
from .util import get_user_group, get_name
from .user import get_user_info, save_user_info
from .explanations import get_top_explanations
from . import config

atri = ATRI()


class ChatModel:
    def __init__(self):
        self.history = {}
        self.rater = {}

    async def get_resp(self, bot, group_id: int, user_id: str) -> str:
        if group_id not in self.rater:
            self.rater[group_id] = RateLimiter(15, 60)
        if not self.rater[group_id].is_allowed():
            return "歇会歇会~~"
        if group_id not in self.history:
            self.history[group_id] = ChatHistory()
        history: ChatHistory = self.history[group_id]
        text = (f"#角色设定\n"
                f"{atri.get_role_prompt()}\n")
        text += f"#用户历史聊天记录\n"
        history_list = history.get_history()
        if history_list:
            text += '\n'.join([f"<{h.time}>{await get_name(bot, h.sender, group_id)}:{h.text}" for h in history_list])
        else:
            text += '无聊天记录'
        dialogue_list = history.get_dialogues()
        text += f"\n#最近对话记录\n"
        if dialogue_list:
            text += '\n'.join(
                [f"<{d.time}>{await get_name(bot, d.sender, group_id)}:{d.text}\n你的回复:{d.resp}" for d in
                 dialogue_list])
        else:
            text += '无对话记录'
        user_info = get_user_info(user_id)
        text += (f"\n#当前对话人的信息\n"
                 f"{"Ta是你的主人" if get_user_group(user_id) == "主人" else 'Ta只是你的陪聊对象'}\n"
                 f"好感度:{user_info.love}。正积极,负消极,最大1000,最小-1000,难增加,易减少\n")
        if user_info.memery:
            text += f"与用户的记忆:{user_info.memery}\n"
        text += f"#补充信息\n"
        lst_history = history.get_last_history()
        exp = get_top_explanations(lst_history.text)
        if exp:
            text += "词语解释:\n"
            text += "\n".join(exp)
            text += "\n"
        text += (f"#当前对话信息\n"
                 f"对话人:{await get_name(bot, lst_history.sender, group_id)}\n"
                 f"时间:{lst_history.time}\n"
                 f"内容:{lst_history.text}")
        resp_schema = {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "必填，符合角色设定的简单回复"
                },
                "love": {
                    "type": "integer",
                    "default": 0,
                    "description": "可选，与对话人好感度增减数值"
                },
                "exec": {
                    "type": "array",
                    "default": [],
                    "items": {
                        "type": "string"
                    },
                    "description": "可选，需执行的操作列表"
                },
                "memery": {
                    "type": "string",
                    "description": "可选，新增与该用户重要的记忆（简短的字符串）"
                },
                "del_mem": {
                    "type": "array",
                    "items": {
                        "type": "integer"
                    },
                    "description": "可选，该用户需删除的无用记忆索引"
                }
            },
            "required": ["content"]
        }
        resp = await chat_manager.generate_content_from(config.model, text, 'json', resp_schema)
        if type(resp) == dict:
            love = resp.get('love', 0)
            if love:
                user_info.love = user_info.love + love
            memery = resp.get('memery', None)
            if memery:
                user_info.memery.append(memery)
                if len(user_info.memery) > 10:
                    user_info.memery.pop(0)
            del_mem = resp.get('del_mem', None)
            if del_mem:
                for i in del_mem:
                    try:
                        user_info.memery.pop(i)
                    except Exception:
                        log.warning(f"记忆删除失败->{i}")
            save_user_info(user_id, user_info)
            r_t = resp.get('content', "输出格式错误")
            if 'content' in resp:
                self.history[group_id].add_dialogue(r_t)
            return r_t
        else:
            return resp

    async def add_history(self, group_id, user_id, content):
        if group_id not in self.history:
            self.history[group_id] = ChatHistory()
        self.history[group_id].add_history(user_id, content)


chat_model = ChatModel()
