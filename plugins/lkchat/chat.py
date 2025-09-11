from random import choice

from ATRI import IMG_DIR
from ATRI.log import log
from ATRI.message import img_msg_from_path
from ATRI.utils.limiter import RateLimiter
from ATRI.system.lkapi.ai import chat_manager

from .atri import ATRI
from .history import ChatHistory, Dialogue
from .util import get_user_group, get_name
from .user import get_user_info, save_user_info
from .explanations import get_top_explanations
from . import config

atri = ATRI()


class ChatModel:
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
            },
            "face_text": {
                "type": "string",
                "description": "可选，当需要表达自己心情时填一个心情词来附带一个表情作为回复"
            }
        },
        "required": ["content"]
    }

    def __init__(self):
        self.history = {}
        self.rater = {}

    async def get_agent(self, group_id, user_id, user_info, bot):
        # 角色设定
        text = (f"#角色设定\n"
                f"{atri.get_role_prompt()}\n")
        # 历史聊天记录
        if group_id not in self.history:
            self.history[group_id] = ChatHistory()
        history: ChatHistory = self.history[group_id]
        text += f"#历史聊天记录\n"
        history_list = history.get_history()
        if history_list:
            text += '\n'.join([
                f"<{h.time}>{await get_name(bot, h.sender, group_id)}:{h.text}{f'\n你对此回复:{h.resp}' if type(h) == Dialogue else ''}"
                for h in history_list])
        else:
            text += '无聊天记录'
        # 对话人信息
        text += (f"\n#当前对话人的信息\n"
                 f"{"Ta是你的主人" if get_user_group(user_id) == "主人" else 'TA只是你的陪聊对象不要称呼TA主人'}\n"
                 f"好感度:{user_info.love}。正积极,负消极,最大1000,最小-1000,难增加,易减少\n")
        if user_info.memery:
            text += f"与用户的记忆:{user_info.memery}\n"
        # 补充信息
        text += f"#补充信息\n"
        # 对话中词语解释
        lst_history = history.get_last_history()
        exp = get_top_explanations(lst_history.text)
        if exp:
            text += "词语解释:\n"
            text += "\n".join(exp)
            text += "\n"
        # 当前对话信息
        text += (f"#当前对话信息\n"
                 f"对话人:{await get_name(bot, lst_history.sender, group_id)}\n"
                 f"时间:{lst_history.time}\n"
                 f"内容:{lst_history.text}")
        return text

    async def get_resp(self, bot, group_id: int, user_id: str) -> list[str]:
        if group_id not in self.rater:
            self.rater[group_id] = RateLimiter(15, 60)
        if not self.rater[group_id].is_allowed():
            return ["歇会歇会~~"]
        user_info = get_user_info(user_id)
        text = await self.get_agent(group_id, user_id, user_info, bot)
        resp = await chat_manager.generate_content_from(config.model, text, 'json', self.resp_schema)
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
            msg = [r_t]
            if 'face_text' in resp:
                msg.append(self.get_face(resp['face_text']))
            return msg
        else:
            return [resp]

    async def add_history(self, group_id, user_id, content):
        if group_id not in self.history:
            self.history[group_id] = ChatHistory()
        self.history[group_id].add_history(user_id, content)

    @staticmethod
    def get_face(face_text):
        img_path = IMG_DIR / "atri"
        file = None
        if face_text in ('开心', '高兴', '快乐', '喜悦', '愉快', '狂喜'):
            file = 'KX.jpg'
        elif face_text in ('幸福',):
            file = 'SUKI.jpg'
        elif face_text in ('兴奋', '激动',):
            file = 'XF.png'
        elif face_text in ('满意', '满足',):
            file = 'MY.png'
        elif face_text in ('疑问', '疑惑', '困惑', '怀疑',):
            file = choice(('YW.jpg', 'WH.jpg',))
        elif face_text in ('迷茫', '发呆', '呆住',):
            file = 'DZ.jpg'
        elif face_text in ('沮丧', '失望',):
            file = 'SW.jpg'
        elif face_text in ('生气', '恼火', '愤怒',):
            file = choice(('SQ.jpg', 'QF.gif',))
        if file:
            return img_msg_from_path(img_path / file)
        else:
            if (img_path / f'{face_text}.jpg').exists():
                return img_msg_from_path(img_path / f'{face_text}.jpg')
            return f'[{face_text}].jpg'


chat_model = ChatModel()
