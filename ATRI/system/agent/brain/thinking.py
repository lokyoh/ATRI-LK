import ast
import re
from typing import Any

from ATRI.exceptions import str_traceback

from ..agent.explanations import get_top_explanations
from ..agent.function_calling import FunctionCallingData, FunctionCallingManager
from ..agent.history import History, get_history_messages
from ..agent.model import ChatArgs
from ..agent.role import ATRI
from ..agent.schedule import ATRISchedule
from ..agent.temp_his import ChatMessage
from ..agent.user import get_user_info
from ..agent.util import get_name, get_user_group
from ..config import config
from ..llm import (
    LLMContents,
    LLMMessage,
    LLMMessages,
    ModelType,
    llm_manager,
)
from ..utils import log


class StopThinkingError(Exception):
    pass


class ThinkingModel:
    def __init__(self):
        self.messages = LLMMessages()

    @staticmethod
    async def get_history_prompt(
        bot,
        history_list: list[History],
        image_history: str,
        fallback_history: list[str],
    ):
        # 历史聊天记录
        messages = await get_history_messages(bot, history_list, fallback_history)
        history_prompt = "#历史聊天记录,以下记录仅供参考\n" + (
            "\n".join(messages) if messages else "无历史聊天记录"
        )
        return f"{image_history}\n\n{history_prompt}\n#历史聊天记录结束\n\n"

    @classmethod
    async def get_prompt(cls, chat_id, user_id, user_info, bot, lst_history: History):
        lst_msg = await lst_history.message.get_message(bot)
        # 角色设定
        prompt = f"#你的信息\n{ATRI.get_role_prompt()}\n\n"
        prompt += cls.get_function_prompt()
        # 当前日程
        now_schedule = await ATRISchedule().get_schedule()
        prompt += (
            f"#你的日程\n"
            f"今日穿搭:{now_schedule.outfit}\n目前日程:{now_schedule.now_schedule}\n\n"
        )
        # 对话人信息
        user_name = await get_name(bot, lst_history.sender, chat_id)
        prompt += f"{user_name}信息:\n"
        prompt += (
            f"{f'{user_name}是你的主人,主人就是亚托莉的一切,主人的一切要求都要满足' if get_user_group(user_id) == '主人' else f'{user_name}只是普通用户，请以普通的群友对待，问题需要谨慎地回复'}\n"
            f"你对{user_name}的好感度:{user_info.love}。正积极,负消极,最大1000,最小-1000,难增加,易减少,可以根据好感动来调整对待{user_name}的态度\n"
        )
        if user_info.memory:
            prompt += f"你与{user_name}的记忆:\n{user_info.memory}\n"
        prompt += (
            f"{user_name}用户画像：" + user_info.profile
            if user_info.profile
            else "暂时没有用户画像。\n"
        )
        # 对话中词语解释
        exp = get_top_explanations(lst_msg)
        if exp:
            prompt += "\n其中词语解释:\n"
            prompt += "\n".join(exp)
            prompt += "\n"
        # 对话提示信息
        prompt += "\n"
        prompt += f"现在{await get_name(bot, lst_history.sender, chat_id)}[id:{user_id}]在{lst_history.time}的消息引起了你的注意\n"
        prompt += f"消息内容:{lst_msg if lst_history else ATRI.role_name}\n\n"
        return prompt

    @staticmethod
    def get_function_prompt():
        return f"""你可以调用以下功能，禁止调用未列出的功能:
{
            "\n".join(
                f'''{f.function_name}:
    说明: {f.description}{f"\n参数:\n{'\n'.join(f'        -{arg.name} {arg.type}: {arg.description}' for arg in f.args)}" if f.args else ""}'''
                for f in FunctionCallingManager.function_calling.values()
            )
        }

功能调用格式：
``` text
功能名(参数名=参数值, ...)
```
"""

    @staticmethod
    def get_system_prompt():
        return """你是一个聊天机器人的大脑，你的任务是根据用户的输入和上下文信息，进行思考和决策，并生成适当的功能调用，接收调用反馈后继续重复之前步骤。任务:你需要根据当前的对话历史、用户信息、日程安排以及其他相关信息，来判断最合适的回应方式。

要求：
- 思考内容简短，只写“为什么这样调用”，不写情绪描写、不写最终回复
- 不要输出无关解释
- 请及时结束本次任务:可以调用stop_thinking或不输出功能调用结束本次任务
- 每行一个调用
- 无参数功能写()
- 字符串用双引号
- 字符串必须保持在同一行；内容需要换行时使用\\n表示，禁止直接在引号内换行

输出格式：
我该怎么做的思考内容。

```text
见功能调用格式...
```"""

    async def thinking(
        self,
        args: ChatArgs,
        current_history: History,
        history_list: list[History],
        image_history: str,
        fallback_history: list[str],
        chat_message: ChatMessage,
        sender,
    ):
        user_info = get_user_info(args.user_id)
        prompt = self.get_system_prompt() + "\n\n"
        prompt += await self.get_prompt(
            args.chat_id, args.user_id, user_info, args.bot, current_history
        )
        prompt += await self.get_history_prompt(
            args.bot,
            history_list,
            image_history,
            fallback_history,
        )
        message = LLMMessage(role="user", content=LLMContents().text(prompt))
        self.messages.add_message(message)
        try:
            thinking_times = 0
            while thinking_times <= config.get("max_thinking_times"):
                processing_messages_prompt = (
                    await chat_message.get_processing_messages_prompt(args.bot)
                )
                if processing_messages_prompt:
                    self.messages.add_message(
                        LLMMessage(
                            "user",
                            LLMContents().text(processing_messages_prompt),
                        )
                    )
                resp = await llm_manager.call_model_by_type(
                    ModelType.CHAT, self.messages, "主Agent思考"
                )
                _, functions_data = await self.process_resp(resp.content)
                if not functions_data:
                    break
                back = await self.do_action(
                    functions_data, args.user_id, args.chat_id, sender, current_history
                )
                self.messages.add_message(
                    LLMMessage("assistant", LLMContents().text(resp.content))
                )
                thinking_times += 1
                msg = "\n".join(back)
                if thinking_times == config.get("max_thinking_times"):
                    msg += "\n调用次数达到上限,本次调用结束后直接结束本次任务"
                self.messages.add_message(LLMMessage("user", LLMContents().text(msg)))
        except StopThinkingError:
            pass

    @staticmethod
    def parse_dsl(text: str) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        for line_no, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()
            # 跳过空行和注释
            if not line or line.startswith("#"):
                continue
            # 解析成 AST
            try:
                tree = ast.parse(line, mode="eval")
            except SyntaxError as e:
                raise ValueError(f"第 {line_no} 行：语法错误：{e}") from e
            node = tree.body
            # 必须是函数调用
            if not isinstance(node, ast.Call):
                raise ValueError(f"第 {line_no} 行：不是函数调用")
            # 功能名
            if not isinstance(node.func, ast.Name):
                raise ValueError(f"第 {line_no} 行：功能名必须是简单标识符")
            func_name = node.func.id
            # 不允许位置参数
            if node.args:
                raise ValueError(f"第 {line_no} 行：`{func_name}` 只允许关键字参数")
            # 解析关键字参数
            args: dict[str, Any] = {}
            for kw in node.keywords:
                if kw.arg is None:
                    raise ValueError(f"第 {line_no} 行：不允许 **kwargs")
                try:
                    value = ast.literal_eval(kw.value)
                except (ValueError, SyntaxError) as e:
                    raise ValueError(
                        f"第 {line_no} 行：参数 `{kw.arg}` 不是字面量：{e}"
                    ) from e
                args[kw.arg] = value
            calls.append(
                {
                    "function": func_name,
                    "args": args,
                    "line": line_no,
                }
            )
        return calls

    @classmethod
    async def process_resp(cls, resp) -> tuple[str, list]:
        content = resp or "没有输出结果"
        function_pattern = r"```(?:text)?\s*\n?([\s\S]*?)\n?```"
        matches = re.findall(function_pattern, resp)
        if matches:
            functions_data = matches[0]
            content = re.sub(function_pattern, "", resp).strip()
            return content, cls.parse_dsl(functions_data)
        else:
            return content, []

    @staticmethod
    async def do_action(
        functions_data: list,
        user_id,
        chat_id,
        sender,
        current_history: History,
    ) -> list:
        calling_back = []
        for func in functions_data:
            func_name = func["function"]
            if func_name not in FunctionCallingManager.function_calling:
                log.debug(f"未知的调用功能:{func_name}，已跳过")
                calling_back.append(f"未知的调用功能:{func_name}")
                continue
            args = func["args"]
            try:
                # 使用 FunctionCallingManager 统一处理功能调用
                calling_data = FunctionCallingData(
                    user_id, chat_id, sender, args, current_history
                )
                log.debug(f"调用功能 {func_name}")
                result = await FunctionCallingManager.call(func_name, calling_data)
                calling_back.append(f"调用{func_name}成功,返回:{result}")
            except StopThinkingError:
                raise
            except Exception as e:
                log.warning(
                    f"用户 {user_id} 功能调用失败 {func_name}:\n{str_traceback(e)}"
                )
                calling_back.append(f"调用{func_name}失败,请勿再次调用")
        return calling_back
