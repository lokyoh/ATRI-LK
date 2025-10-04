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

role = ATRI()

atri_face = {
    ('开心', '高兴', '快乐', '喜悦', '愉快', '哈哈',): ['KX.jpg', ],
    ('幸福',): ['SUKI.jpg', ],
    ('兴奋', '激动', '欢呼',): ['XF.png', ],
    ('满意', '满足',): ['MY.png', ],
    ('疑问', '疑惑', '困惑', '怀疑',): ['YW.png', 'WH.jpg', 'YW1.jpg', ],
    ('迷茫', '发呆', '呆住',): ['DZ.jpg', ],
    ('沮丧', '失落', '失望',): ['SW.png', 'SW1.jpg', 'TQ.png', ],
    ('生气', '恼火', '愤怒',): ['SQ.jpg', 'QF.gif', 'SQ1.jpg', ],
    ('好奇',): ['HQ.jpg', ],
    ('傲慢', '自信',): ['AM.jpg', ],
    ('思考',): ['SK.gif', 'SK2.gif', ],
    ('晚安',): ['WA.jpg', ],
    ('得意',): ['DY.gif', 'DY1.gif', ],
    ('元气', '活力四射', '活力',): ['DT.jpg', ],
    ('期待',): ['QD.jpg', ],
    ('关心', '担心',): ['GX.png', ],
    ('摸摸头', '抱抱', '摸摸', '摸', '拥抱', '摸头', '安慰',): ['MMT.png', ],
    ('惊', '惊讶', '吃惊', '吓', '惊吓', '震惊',): ['CJ.jpg', 'CJ1.jpg', 'CJ2.jpg', 'CJ.png', 'CJ1.png', ],
    ('可爱',): ['KA.jpg', ],
    ('哭笑',): ['X.png', ],
    ('星星眼',): ['XXY.png', ],
    ('吐舌',): ['TS.jpg', ],
    ('伤心', '悲伤', '悲',): ['SX.png', ],
    ('流泪', '落泪',): ['K.png', ],
    ('哭', '哭泣', '抽泣',): ['KQ.png', ],
    ('大哭',): ['DK.png', ],
    ('斜眼笑',): ['XYX.jpg', ],
    ('害羞', '羞', '羞耻', '羞涩',): ['HX.gif', ],
    ('羡慕',): ['XM.jpg', 'XM1.jpg', ],
    ('流口水', '馋', '嘴馋',): ['LKS.png', ],
    ('害怕', '恐惧',): ['HP.png', 'HP1.jpg', ],
}


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
                "description": "可选，当需要表达自己心情时，填写心情词后发送对应表情"
            }
        },
        "required": ["content"]
    }

    def __init__(self):
        self.history = {}
        self.rater = {}

    async def get_prompt(self, group_id, user_id, user_info, bot):
        prompt = "在网络群聊环境中中扮演用户与其他用户进行聊天\n"
        # 角色设定
        prompt += (f"#角色设定\n"
                   f"{role.get_role_prompt()}\n")
        # 历史聊天记录
        if group_id not in self.history:
            self.history[group_id] = ChatHistory()
        history: ChatHistory = self.history[group_id]
        prompt += f"#历史聊天记录\n"
        history_list = history.get_history()
        if history_list:
            prompt += '\n'.join([
                f"<{h.time}>{await get_name(bot, h.sender, group_id)}:{h.text}{f'\n你的回复:{h.resp}' if type(h) == Dialogue else ''}"
                for h in history_list])
        else:
            prompt += '无聊天记录'
        # 对话人信息
        lst_history = history.get_last_history()
        prompt += (f"\n#当前对话人信息\n"
                   f"{"Ta是你的主人" if get_user_group(user_id) == "主人" else 'TA只是你的陪聊对象不要称呼Ta主人'}\n"
                   f"你对Ta的好感度:{user_info.love}。正积极,负消极,最大1000,最小-1000,难增加,易减少\n")
        if user_info.memery:
            prompt += f"你与Ta的记忆:{user_info.memery}\n"
        # 对话提示信息
        prompt += f"#对话提示信息\n"
        # 对话中词语解释
        exp = get_top_explanations(lst_history.text)
        if exp:
            prompt += "#词语解释:\n"
            prompt += "\n".join(exp)
            prompt += "\n"
        # 当前对话信息
        prompt += (f"#当前对话\n"
                   f"昵称:{await get_name(bot, lst_history.sender, group_id)}\n"
                   f"时间:{lst_history.time}\n"
                   f"内容:{lst_history.text}")
        return prompt

    async def get_resp(self, bot, group_id: int, user_id: str) -> list[str]:
        group_id = int(group_id)
        user_id = str(user_id)
        if group_id not in self.rater:
            self.rater[group_id] = RateLimiter(15, 60)
        if not self.rater[group_id].is_allowed():
            return ["歇会歇会~~"]
        user_info = get_user_info(user_id)
        text = await self.get_prompt(group_id, user_id, user_info, bot)
        resp = await chat_manager.generate_content_from(config.model, text, 'json', self.resp_schema)
        if type(resp) == dict:
            return self.process_resp(resp, user_id, user_info, group_id)
        else:
            return [resp]

    def process_resp(self, resp: dict, user_id: str, user_info, group_id) -> list:
        love = resp.get('love', 0)
        if love:
            user_info.love = user_info.love + love
        memery = resp.get('memery', None)
        if memery:
            user_info.memery.append(memery)
            if len(user_info.memery) > 10:
                user_info.memery.pop(0)
        del_mem = resp.get('del_mem', [])
        if del_mem:
            for i in del_mem:
                try:
                    user_info.memery.pop(i)
                except Exception:
                    log.warning(f"{user_id}记忆删除失败位置->{i}")
        save_user_info(user_id, user_info)
        r_t = resp.get('content', "输出格式错误")
        if 'content' in resp:
            self.history[group_id].add_dialogue(r_t)
        msg = [r_t]
        if 'face_text' in resp:
            msg.append(self.get_face(resp['face_text']))
        return msg

    async def add_history(self, group_id: int, user_id: str, content: str):
        group_id = int(group_id)
        user_id = str(user_id)
        if group_id not in self.history:
            self.history[group_id] = ChatHistory()
        self.history[group_id].add_history(user_id, content)

    @staticmethod
    def get_face(face_text: str):
        img_path = IMG_DIR / "atri"
        file = None
        for key, value in atri_face.items():
            if face_text in key:
                if len(value) != 0:
                    file = choice(value)
        if file:
            if (img_path / file).exists():
                log.debug(f'发送表情`{face_text}`')
                return img_msg_from_path(img_path / file)
            else:
                log.error(f'缺失文件`{img_path / file}`')
                return f'`{file}`表情文件缺失,请检查本地文件!'
        else:
            if (img_path / f'{face_text}.jpg').exists():
                return img_msg_from_path(img_path / f'{face_text}.jpg')
            log.warning(f'没有`{face_text}`所对应的表情请,请等待更新或手动添加`{face_text}.jpg`至`{img_path}`目录下')
            return f'[{face_text}]'


chat_model = ChatModel()
