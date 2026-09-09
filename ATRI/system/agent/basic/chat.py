import json
import re

from nonebot.adapters.onebot.v11 import Message

from ATRI.exceptions import str_traceback
from ATRI.log import log

from ..agent.atri import ATRI
from ..agent.explanations import get_top_explanations
from ..agent.function_calling import FunctionCallingData, FunctionCallingManager
from ..agent.history import ChatHistory, ImageHistory, chat_history, img_history
from ..agent.sender import ChatSender, QQChatSender
from ..agent.user import get_user_info
from ..agent.util import get_name, get_user_group
from ..llm import ModelType, llm_manager

role = ATRI()


class ChatModel:
    def __init__(self):
        self.rater = {}

    @staticmethod
    async def get_history_prompt(group_id, bot):
        if group_id not in img_history:
            img_history[group_id] = ImageHistory()
        if group_id not in chat_history:
            chat_history[group_id] = ChatHistory()
        history_list = list(chat_history[group_id].get_history()[:-1])
        # 历史聊天记录
        history_prompt = "#历史聊天记录\n"
        if history_list:
            messages = [await h.get_message(bot, False) for h in history_list]
            history_prompt += "\n".join(messages)
        else:
            history_prompt += "无历史聊天记录"
        img_prompt = img_history[group_id].get_history()
        return f"{img_prompt}\n\n{history_prompt}\n\n"

    @staticmethod
    async def get_prompt(group_id, user_id, user_info, bot):
        lst_history = chat_history[group_id].get_last_history()
        lst_msg = await lst_history.message.get_message(bot)
        # 对话提示信息
        prompt = f"现在{await get_name(bot, lst_history.sender, group_id)}在{lst_history.time}的消息引起了你的注意\n"
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
            f"{f'{user_name}是你的主人,主人就是亚托莉的一切,主人的一切要求都要满足' if get_user_group(user_id) == '主人' else f'{user_name}只是普通用户，请以普通的群友对待，问题需要谨慎地回复'}\n"
            f"你对{user_name}的好感度:{user_info.love}。正积极,负消极,最大1000,最小-1000,难增加,易减少,可以根据好感动来调整对待{user_name}的态度\n"
        )
        if user_info.memery:
            prompt += f"你与{user_name}的记忆:\n{user_info.memery}\n"
        prompt += "\n"
        # 角色设定
        prompt += f"#你的信息\n{role.get_role_prompt()}\n\n"
        return prompt

    @staticmethod
    def get_function_prompt():
        return f"""
你有以下功能:
{
            "\n".join(
                f'''{f.function_name}:
    说明: {f.description}
    参数:
{"\n".join(f"        -{arg.name} {arg.type}: {arg.description}" for arg in f.args)}'''
                for f in FunctionCallingManager.chat_functions
            )
        }

需要使用功能时生成以下json结构:
{{
    "function": "功能名",
    "data": {{
        "参数": 参数值
    }}
}}

你需要输出回复与功能调用的列表。
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

    async def reply(self, bot, group_id, user_id, sender: ChatSender):
        group_id = str(group_id)
        user_id = str(user_id)
        user_info = get_user_info(user_id)
        prompt = await self.get_history_prompt(group_id, bot)
        prompt += await self.get_prompt(group_id, user_id, user_info, bot)
        prompt += self.get_function_prompt()
        resp = await llm_manager.call_model_by_type(ModelType.CHAT, prompt)
        response, continue_chat, calling_back = await self.process_resp(
            resp.content, user_id, group_id
        )
        msg = Message()
        for m in response:
            msg.append(m)
        plain_text = msg.extract_plain_text()
        chat_history[group_id].add_reply(plain_text)
        await sender.send(msg)
        if continue_chat:
            times = 1
            function_calling_data = [f"你回复了{response}"]
            function_calling_data += calling_back
            while True:
                try:
                    user_info = get_user_info(user_id)
                    prompt = await self.get_prompt(group_id, user_id, user_info, bot)
                    prompt += "\n\n".join(function_calling_data)
                    prompt += "\n\n请继续回复"
                    if times == 5:
                        prompt += "\n\n重复调用功能次数已达上限"
                    else:
                        prompt + self.get_function_prompt()
                    resp = await llm_manager.call_model_by_type(ModelType.CHAT, prompt)
                    response, continue_chat, calling_back = await self.process_resp(
                        resp.content, user_id, group_id
                    )
                    msg = Message()
                    for m in response:
                        msg.append(m)
                    plain_text = msg.extract_plain_text()
                    chat_history[group_id].add_reply(plain_text)
                    await sender.send(msg)
                    if not continue_chat or times >= 5:
                        break
                    function_calling_data.append(f"你回复了{response}")
                    function_calling_data += calling_back
                except Exception as e:
                    log.error(str_traceback(e))
                    raise
                times += 1
        if isinstance(sender, QQChatSender):
            await sender.finish()

    @staticmethod
    async def process_resp(resp: str, user_id, group_id) -> tuple[list, bool, list]:
        """
        处理模型响应，从字符串中解析 function+data 结构并执行相应操作

        :param resp: 模型响应字符串，包含文本内容和 ```json``` 代码块
        :param user_id: 用户 ID
        :param group_id: 群 ID
        :return: 回复消息列表
        """
        continue_chat = False
        calling_back = []
        # 默认回复内容为整个响应字符串
        content = resp or "没有输出结果"
        functions_data = []
        # 尝试从响应中提取 JSON 代码块
        # 匹配 ```json [...] ``` 或 ``` [...] ``` 格式
        json_pattern = r"```(?:json)?\s*\n?([\s\S]*?)\n?```"
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
            content = re.sub(json_pattern, "", resp).strip()
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
            func_name = func.get("function", None)
            if not func_name:
                continue
            data = func.get("data", {})
            try:
                # 使用 FunctionCallingManager 统一处理功能调用
                calling_data = FunctionCallingData(user_id, group_id, data)
                log.debug(f"调用功能 {func_name}")
                result = await FunctionCallingManager.call(func_name, calling_data)
                # 如果功能调用有返回值（如 send_face），添加到消息列表
                if result is not None:
                    if FunctionCallingManager.is_continue_calling(func_name):
                        calling_back.append(
                            f"你调用了{func_name}并得到响应：\n{result}"
                        )
                        continue_chat = True
                    else:
                        msg.append(result)
            except Exception as e:
                log.warning(
                    f"用户 {user_id} 功能调用失败 {func_name}:\n{str_traceback(e)}"
                )
        # 将文本内容添加到消息列表
        if content:
            msg.insert(0, content)
        return msg, continue_chat, calling_back


chat_model = ChatModel()
