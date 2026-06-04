import json
import re

from nonebot.adapters.onebot.v11 import Message

from ATRI.log import log
from ATRI.exceptions import str_traceback

from ..agent.atri import ATRI
from ..agent.function_calling import ReplyFunctionCallingManager, FunctionCallingData
from ..agent.history import chat_history
from ..agent.sender import QQChatSender, ChatSender
from ..agent.util import get_user_group, get_name
from ..agent.user import get_user_info
from ..llm import llm_manager, ModelType
from ..config import config
from ..llm.tts import generate_audio


class ReplyModel:
    @staticmethod
    async def get_prompt(group_id, user_id, user_info, bot):
        lst_history = chat_history[group_id].get_last_history()
        lst_msg = await lst_history.message.get_message(bot)
        # 对话提示信息
        prompt = f"现在{await get_name(bot, lst_history.sender, group_id)}[id:{user_id}]在{lst_history.time}的消息引起了你的注意\n"
        prompt += f"消息内容:{lst_msg}\n"
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
    def get_function_prompt(thinking_list, calling_backs):
        return f"""
你有以下功能，请不要调用你没拥有的功能:
{"\n".join(
            f"""{f.function_name}:
    说明: {f.description}
    参数:
{"\n".join(f"        -{arg.name} {arg.type}: {arg.description}" for arg in f.args)}""" for f in ReplyFunctionCallingManager.chat_functions
        )}

需要使用功能时生成以下json结构:
{{
    "function": "功能名",
    "data": {{
        "参数": 参数值
    }}
}}

#{"\n".join(thinking_list)}

#功能调用
{"\n".join(calling_backs)}

你需要根据思考器思考的回复指导来输出回复与自己功的能调用的列表。
请注意回复尽量简洁，只需能表达自己的意思即可，回复风格参考贴吧百度，不要在此出现功能调用，不要带有代码段与调试信息。
功能调用时请严格使用json结构，请勿使用其他格式。

输出结构示例:
你也早上好呀!

```
[
    {{
        "function": "send_face",
        "data": {{
            "meme": "smiling"
        }}
    }}
]
```"""

    @classmethod
    async def reply(cls, bot, group_id, user_id, thinking_list, calling_backs, sender: ChatSender, with_tts = False):
        group_id = str(group_id)
        user_id = str(user_id)
        user_info = get_user_info(user_id)
        prompt = await cls.get_prompt(group_id, user_id, user_info, bot)
        prompt += cls.get_function_prompt(thinking_list, calling_backs)
        resp = await llm_manager.call_model_by_type(ModelType.CHAT, prompt)
        response = await cls.process_resp(resp.get('content'), user_id, group_id)
        msg = Message()
        for m in response:
            msg.append(m)
        plain_text = msg.extract_plain_text()
        chat_history[group_id].add_reply(plain_text)
        await sender.send(msg)
        if config.tts.enable and with_tts and plain_text and len(plain_text) <= 300:
            if path := await generate_audio(plain_text):
                from ATRI.message import rec_msg_from_path
                await sender.send(rec_msg_from_path(path))
        if isinstance(sender, QQChatSender):
            await sender.finish()

    @staticmethod
    async def process_resp(resp: str, user_id, group_id) -> list:
        """
        处理模型响应，从字符串中解析 function+data 结构并执行相应操作
        """
        # 默认回复内容为整个响应字符串
        content = resp or "没有输出结果"
        functions_data = []
        # 尝试从响应中提取 JSON 代码块
        # 匹配 ```json [...] ``` 或 ``` [...] ``` 格式
        json_pattern = r'```(?:json)?\s*\n?([\s\S]*?)\n?```'
        matches = re.findall(json_pattern, resp)
        if matches:
            # 有代码块，提取第一个有效的 JSON
            for match in matches:
                try:
                    functions_data = json.loads(match.strip())
                    break
                except json.JSONDecodeError:
                    continue
            # 去除原文本中的代码块，保留纯文本内容
            content = re.sub(json_pattern, '', resp).strip()
        else:
            # 没有代码块，尝试直接解析整个响应为 JSON
            try:
                functions_data = json.loads(resp)
            except json.JSONDecodeError:
                # 无法解析，保持原文本
                pass
        # 确保 functions 是列表
        if isinstance(functions_data, dict):
            functions_data = [functions_data]
        # 处理功能调用
        msg = []
        for func in functions_data:
            if not isinstance(func, dict):
                continue
            func_name = func.get('function', None)
            if not func_name:
                continue
            if func_name not in ReplyFunctionCallingManager.chat_functions:
                log.debug(f"未知的调用功能:{func_name}，已跳过")
                continue
            data = func.get('data', {})
            try:
                calling_data = FunctionCallingData(user_id, group_id, data)
                log.debug(f"调用功能 {func_name}")
                result = await ReplyFunctionCallingManager.call(func_name, calling_data)
                if result is not None:
                    msg.append(result)
            except Exception as e:
                log.warning(f"用户 {user_id} 功能调用失败 {func_name}:\n{str_traceback(e)}")
        # 将文本内容添加到消息列表
        if content:
            msg.insert(0, content)
        return msg
