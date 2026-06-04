import re
import json

from ..agent.atri import ATRI
from ..agent.function_calling import FunctionCallingManager
from ..agent.history import ChatHistory, ImageHistory, chat_history, img_history
from ..agent.util import get_user_group, get_name
from ..agent.user import get_user_info
from ..agent.explanations import get_top_explanations
from ..llm import llm_manager, ModelType


class ThinkingModel:

    @staticmethod
    async def get_history_prompt(group_id, bot):
        if group_id not in img_history:
            img_history[group_id] = ImageHistory()
        if group_id not in chat_history:
            chat_history[group_id] = ChatHistory()
        history_list = list(chat_history[group_id].get_history()[:-1])
        # 历史聊天记录
        history_prompt = f"#历史聊天记录\n"
        if history_list:
            messages = [await h.get_message(bot) for h in history_list]
            history_prompt += "\n".join(messages)
        else:
            history_prompt += "无历史聊天记录"
        img_prompt = img_history[group_id].get_history()
        return f"{img_prompt}\n\n{history_prompt}\n#聊天记录结束\n\n"

    @staticmethod
    async def get_prompt(group_id, user_id, user_info, bot):
        lst_history = chat_history[group_id].get_last_history()
        lst_msg = await lst_history.message.get_message(bot)
        # 对话提示信息
        prompt = f"现在{await get_name(bot, lst_history.sender, group_id)}[id:{user_id}]在{lst_history.time}的消息引起了你的注意\n"
        prompt += f"消息内容:{lst_msg}\n"
        # 对话中词语解释
        exp = get_top_explanations(lst_msg)
        if exp:
            prompt += "其中词语解释:\n"
            prompt += "\n".join(exp)
        # 对话人信息
        user_name = await get_name(bot, lst_history.sender, group_id)
        prompt += f"\n{user_name}信息:\n"
        prompt += (
            f"{f"{user_name}是你的主人,主人就是亚托莉的一切,主人的一切要求都要满足" if get_user_group(user_id) == "主人" else f'{user_name}只是普通用户，请以普通的群友对待，问题需要谨慎地回复'}\n"
            f"你对{user_name}的好感度:{user_info.love}。正积极,负消极,最大1000,最小-1000,难增加,易减少,可以根据好感动来调整对待{user_name}的态度\n")
        if user_info.memery:
            prompt += f"你与{user_name}的记忆:\n{user_info.memery}\n"
        prompt += f"{user_name}用户画像：" + user_info.profile or "暂时没有用户画像。\n"
        prompt += "\n"
        # 角色设定
        prompt += (f"#你的信息\n"
                   f"{ATRI.get_role_prompt()}\n\n")
        return prompt

    @staticmethod
    def get_function_prompt():
        return f"""
你有以下功能:
{"\n".join(
            f"""{f.function_name}:
    说明: {f.description}
    参数:
{"\n".join(f"        -{arg.name} {arg.type}: {arg.description}" for arg in f.args)}""" for f in FunctionCallingManager.chat_functions
        )}

需要使用功能时生成以下json结构:
{{
    "function": "功能名",
    "data": {{
        "参数": 参数值
    }}
}}

"""
    @staticmethod
    def get_resp_prompt():
        return """你需要输出思考过程与功能调用的列表。
思考过程首先分析你的聊天对象与聊天时间，再分析聊天历史总结发生了什么，然后根据当前对话分析需要调用的功能，最后再指导如何回复。
功能调用时请严格使用json结构，请勿使用其他格式。

输出结构示例:
此处替换思考过程。
```json
[
    {
        "function": "love_change",
        "data": {
            "num": 1
        }
    }
]
```"""

    @staticmethod
    def get_continue_resp_prompt():
        return """你需要输出再思考过程与功能调用的列表。
    再思考过程首先分析现在需要新调用的功能，最后再重新指导如何回复。
    功能调用时请严格使用json结构，请勿使用其他格式。

    输出结构示例:
    此处替换再思考过程。
    ```json
    [
        {
            "function": "love_change",
            "data": {
                "num": 1
            }
        }
    ]
    ```"""

    @classmethod
    async def thinking(cls, bot, group_id, user_id, str_sensory) -> tuple[str, list]:
        user_info = get_user_info(user_id)
        prompt = await cls.get_history_prompt(group_id, bot)
        prompt += await cls.get_prompt(group_id, user_id, user_info, bot)
        prompt += f"#语境分析:\n{str_sensory}\n\n"
        prompt += cls.get_function_prompt()
        prompt += cls.get_resp_prompt()
        resp = await llm_manager.call_model_by_type(ModelType.CHAT, prompt)
        return await cls.process_resp(resp.get('content'))

    @classmethod
    async def continue_thinking(cls, bot, group_id, user_id, function_calling_data, stop_calling) -> tuple[str, list]:
        user_info = get_user_info(user_id)
        prompt = await cls.get_prompt(group_id, user_id, user_info, bot)
        prompt += "\n\n".join(function_calling_data)
        prompt += "\n"
        if stop_calling:
            prompt += "\n重复调用功能次数已达上限，请给出最终的回复指导"
        else:
            prompt + cls.get_function_prompt()
            prompt += cls.get_continue_resp_prompt()
        resp = await llm_manager.call_model_by_type(ModelType.CHAT, prompt)
        return await cls.process_resp(resp.get('content'))

    @classmethod
    async def process_resp(cls, resp) -> tuple[str, list]:
        content = resp or "没有输出结果"
        functions_data = []
        json_pattern = r'```(?:json)?\s*\n?([\s\S]*?)\n?```'
        matches = re.findall(json_pattern, resp)
        if matches:
            for match in matches:
                try:
                    functions_data = json.loads(match.strip())
                    break
                except json.JSONDecodeError:
                    continue
            content = re.sub(json_pattern, '', resp).strip()
        else:
            try:
                functions_data = json.loads(resp)
            except json.JSONDecodeError:
                pass
        if isinstance(functions_data, dict):
            functions_data = [functions_data]
        return content, functions_data
