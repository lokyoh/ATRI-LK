import json
import re

from ..agent.explanations import get_top_explanations
from ..agent.function_calling import FunctionCallingManager
from ..agent.history import History, get_history_messages
from ..agent.role import ATRI
from ..agent.schedule import ATRISchedule
from ..agent.user import get_user_info
from ..agent.util import get_name, get_user_group
from ..llm import ModelType, llm_manager


class ThinkingModel:
    @staticmethod
    async def get_history_prompt(
        bot,
        history_list: list[History],
        image_history: str,
        fallback_history: list[str],
        processing_messages_prompt: str,
    ):
        # 历史聊天记录
        messages = await get_history_messages(bot, history_list, fallback_history)
        history_prompt = "#历史聊天记录\n" + (
            "\n".join(messages) if messages else "无历史聊天记录"
        )
        return (
            f"{image_history}\n\n{history_prompt}\n#聊天记录结束\n\n"
            f"{processing_messages_prompt}\n"
        )

    @staticmethod
    async def get_prompt(chat_id, user_id, user_info, bot, lst_history: History):
        lst_msg = await lst_history.message.get_message(bot)
        # 对话提示信息
        prompt = f"现在{await get_name(bot, lst_history.sender, chat_id)}[id:{user_id}]在{lst_history.time}的消息引起了你的注意\n"
        prompt += f"消息内容:{lst_msg}\n"
        # 对话中词语解释
        exp = get_top_explanations(lst_msg)
        if exp:
            prompt += "其中词语解释:\n"
            prompt += "\n".join(exp)
        # 对话人信息
        user_name = await get_name(bot, lst_history.sender, chat_id)
        prompt += f"\n{user_name}信息:\n"
        prompt += (
            f"{f'{user_name}是你的主人,主人就是亚托莉的一切,主人的一切要求都要满足' if get_user_group(user_id) == '主人' else f'{user_name}只是普通用户，请以普通的群友对待，问题需要谨慎地回复'}\n"
            f"你对{user_name}的好感度:{user_info.love}。正积极,负消极,最大1000,最小-1000,难增加,易减少,可以根据好感动来调整对待{user_name}的态度\n"
        )
        if user_info.memery:
            prompt += f"你与{user_name}的记忆:\n{user_info.memery}\n"
        prompt += f"{user_name}用户画像：" + user_info.profile or "暂时没有用户画像。\n"
        prompt += "\n"
        # 角色设定
        prompt += f"#你的信息\n{ATRI.get_role_prompt()}\n\n"
        # 当前日程
        now_schedule = await ATRISchedule().get_schedule()
        prompt += (
            f"#你的日程\n"
            f"今日穿搭:{now_schedule.outfit}\n目前日程:{now_schedule.now_schedule}\n\n"
        )
        return prompt

    @staticmethod
    def get_function_prompt():
        return f"""
你可以调用以下功能，禁止调用未列出的功能:
{
            "\n".join(
                f'''{f.function_name}:
    说明: {f.description}
    参数:
{"\n".join(f"        -{arg.name} {arg.type}: {arg.description}" for arg in f.args)}'''
                for f in FunctionCallingManager.chat_functions
            )
        }

功能调用格式：
``` json
[
    {{
        "function": "功能名",
        "data": {{
            "参数": 参数值
        }}
    }}
]
```
"""

    @staticmethod
    def get_resp_prompt():
        return """请先简短思考，再给出功能调用。

思考必须包含：
1. 聊天对象与时间
2. 历史聊天总结
3. 当前是否需要调用功能
4. 应该如何回复

要求：
- 思考要短，不要展开成长篇分析。
- 功能调用必须是合法 JSON。
- 不需要调用功能时，输出空数组 []。
- 不要输出无关解释。

输出格式：
简短思考过程。

```json
[
    ...
]
```"""

    @staticmethod
    def get_continue_resp_prompt():
        return """请进行简短再思考，并给出新增功能调用。

要求：
- 只分析现在还需要调用什么新功能。
- 不要重复已经调用过的功能。
- 最后重新给出回复指导。
- 功能调用必须是合法 JSON。
- 不需要新增功能时，输出空数组 []。

输出格式：
简短再思考过程。

```json
[
    ...
]
```"""

    @classmethod
    async def thinking(
        cls,
        bot,
        chat_id,
        user_id,
        str_sensory,
        current_history: History,
        history_list: list[History],
        image_history: str,
        fallback_history: list[str],
        chat_message,
    ) -> tuple[str, list]:
        user_info = get_user_info(user_id)
        prompt = await cls.get_history_prompt(
            bot,
            history_list,
            image_history,
            fallback_history,
            await chat_message.get_processing_messages_prompt(bot),
        )
        prompt += await cls.get_prompt(
            chat_id, user_id, user_info, bot, current_history
        )
        if not str_sensory is None:
            prompt += f"#语境分析:\n{str_sensory}\n\n"
        prompt += cls.get_function_prompt()
        prompt += cls.get_resp_prompt()
        resp = await llm_manager.call_model_by_type(ModelType.CHAT, prompt)
        return await cls.process_resp(resp.content)

    @classmethod
    async def continue_thinking(
        cls,
        bot,
        chat_id,
        user_id,
        function_calling_data,
        stop_calling,
        current_history: History,
        chat_message,
    ) -> tuple[str, list]:
        user_info = get_user_info(user_id)
        prompt = await cls.get_prompt(chat_id, user_id, user_info, bot, current_history)
        prompt += await chat_message.get_processing_messages_prompt(bot)
        prompt += "\n\n".join(function_calling_data)
        prompt += "\n"
        if stop_calling:
            prompt += "\n重复调用功能次数已达上限，请给出最终的回复指导"
        else:
            prompt + cls.get_function_prompt()
            prompt += cls.get_continue_resp_prompt()
        resp = await llm_manager.call_model_by_type(ModelType.CHAT, prompt)
        return await cls.process_resp(resp.content)

    @classmethod
    async def process_resp(cls, resp) -> tuple[str, list]:
        content = resp or "没有输出结果"
        functions_data = []
        json_pattern = r"```(?:json)?\s*\n?([\s\S]*?)\n?```"
        matches = re.findall(json_pattern, resp)
        if matches:
            for match in matches:
                try:
                    functions_data = json.loads(match.strip())
                    break
                except json.JSONDecodeError:
                    continue
            content = re.sub(json_pattern, "", resp).strip()
        else:
            try:
                functions_data = json.loads(resp)
            except json.JSONDecodeError:
                pass
        if isinstance(functions_data, dict):
            functions_data = [functions_data]
        return content, functions_data
