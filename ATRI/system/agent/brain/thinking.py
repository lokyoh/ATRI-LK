import time
from typing import Any

from ATRI.exceptions import str_traceback
from ATRI.utils.datetime import now

from ..agent.explanations import get_top_explanations
from ..agent.function_calling import FunctionCallingData, FunctionCallingManager
from ..agent.history import History, get_history_messages
from ..agent.memory.persona import render_memories, render_self_memories
from ..agent.model import ChatArgs
from ..agent.role import ATRI
from ..agent.schedule import ATRISchedule
from ..agent.skills import skill_manager
from ..agent.state import (
    fatigue_for_hour,
    get_state,
    set_fatigue,
    settle_from_turn,
    state_store,
)
from ..agent.telemetry import telemetry
from ..agent.temp_his import ChatMessage
from ..agent.user import (
    bump_familiarity,
    describe_relation,
    get_self_memories,
    get_user_info,
)
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
from .action_parser import normalize_args, parse_calls, split_response


def _sync_attention(current_activity: str) -> None:
    """把日程里的「当前活动」写进内心状态。

    只在变化时落库；日程的 Null / 睡眠占位不算「正忙」，跳过。
    """
    try:
        text = str(current_activity or "").strip()
        if not text or text in {"Null", "睡眠中。", "睡眠中"}:
            return
        state = get_state()
        if state.attention != text:
            state.attention = text
            state_store.save(state)
    except Exception:
        pass


def get_state_prompt() -> str:
    """把内心状态渲染成提示词片段。

    疲劳每轮按当前时段**自动重算**：它由作息决定，不是模型能自由决定的，
    也不该只在模型主动调 ``feel`` 时才更新。
    """
    try:
        hour = now().hour
        current = get_state()
        if abs(current.fatigue - fatigue_for_hour(hour)) > 0.05:
            current = set_fatigue(fatigue_for_hour(hour))
        text = current.describe()
    except Exception:
        return ""
    if not text:
        return ""
    return f"#你此刻的状态\n{text}\n"


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
    async def get_prompt(
        cls,
        chat_id,
        user_id,
        user_info,
        bot,
        lst_history: History,
        sender=None,
        initiative: bool = False,
    ):
        """拼装思考提示词。

        :param initiative: **主动开口**模式。此时没有对话人、没有当前消息，
            但其余上下文（角色、技能、日程、状态、自我记忆、工具）全部保留——
            这正是「用全量 thinking 来开口」与「用轻量提示词开口」的区别：
            她有完整的自我认知和工具能力，只是这次没有人在跟她说话。
        """
        lst_msg = await lst_history.message.get_message(bot) if lst_history else ""
        # 角色设定
        prompt = f"#你的信息\n{ATRI.get_role_prompt()}\n\n"
        prompt += cls.get_function_prompt(
            FunctionCallingManager.get_available(sender, user_id)
        )
        # 内嵌技能正文
        embedded = skill_manager.get_embedded_prompt()
        if embedded:
            prompt += f"#你的技能\n{embedded}\n\n"
        now_schedule = await ATRISchedule().get_schedule()
        prompt += f"#今日穿搭\n{now_schedule.outfit}\n"
        _sync_attention(now_schedule.now_schedule)
        state_text = get_state_prompt()
        if state_text:
            prompt += state_text
        if initiative:
            prompt += (
                "#你现在的情况\n"
                "这个群现在没有人在跟你说话。你可以选择主动说点什么，"
                "也可以什么都不说（直接调用 stop_thinking）。\n"
            )
        else:
            # 对话人信息
            user_name = await get_name(bot, lst_history.sender, chat_id)
            prompt += f"#{user_name}信息:\n"
            prompt += (
                f"{f'{user_name}是你的主人,主人就是亚托莉的一切,主人的一切要求都要满足' if get_user_group(user_id) == '主人' else f'{user_name}只是普通用户，请以普通的群友对待，问题需要谨慎地回复'}\n"
                f"你和{user_name}的关系：{describe_relation(user_info)}\n"
            )
            if user_info.memory:
                # 渲染成回忆的口吻，而不是把 Python 列表原样拼进提示词。
                memory_text = render_memories(user_name, user_info.memory)
                if memory_text:
                    prompt += f"#你记得的事\n{memory_text}\n"
        # 她自己的经历：这是「独立个体」在提示词里的落点。
        # 与「关于用户的事」分开渲染——前者可以自然流露，后者要看场合。
        # **主动开口时这条更重要**：她的素材主要来自这里。
        self_memories = get_self_memories()
        if self_memories:
            self_text = render_self_memories(self_memories)
            if self_text:
                prompt += f"\n{self_text}\n"
        if not initiative:
            prompt += (
                f"{user_name}用户画像：" + user_info.profile
                if user_info.profile
                else "暂时没有用户画像。\n"
            )
        # 对话中词语解释（只对真实消息有意义）
        exp = get_top_explanations(lst_msg) if lst_msg else []
        if exp:
            prompt += "\n其中词语解释:\n"
            prompt += "\n".join(exp)
            prompt += "\n"
        # 对话提示信息
        prompt += "\n"
        if initiative:
            prompt += (
                "现在没有人 @ 你，也没有可以回应的消息。\n"
                "如果你想说话，就直接调用 send_message 把话说出来；"
                "如果此刻没什么想说的，调用 stop_thinking。\n\n"
            )
        else:
            prompt += f"现在{await get_name(bot, lst_history.sender, chat_id)}[id:{user_id}]在{lst_history.time}的消息引起了你的注意\n"
            prompt += f"消息内容:{lst_msg if lst_history else ATRI.role_name}\n\n"
        return prompt

    @staticmethod
    def get_function_prompt(available: dict[str, type] | None = None):
        if available is None:
            available = FunctionCallingManager.get_available()
        return f"""你可以调用以下功能，禁止调用未列出的功能:
{
            "\n".join(
                f'''{f.function_name}:
    说明: {f.description}{f"\n    参数:\n{'\n'.join(f'        -{arg.name} {arg.type}: {arg.description}' for arg in f.args)}" if f.args else ""}'''
                for f in available.values()
            )
        }

功能调用格式：
功能名(参数名=参数值, ...)
"""

    @staticmethod
    def get_system_prompt():
        return """你是一个聊天机器人的大脑，你的任务是根据用户的输入和上下文信息，进行思考和决策，并生成适当的功能调用，接收调用反馈后继续重复之前步骤。任务:你需要根据当前的对话历史、用户信息、日程安排以及其他相关信息，来判断最合适的回应方式。

要求：
- 思考内容简短，只写“为什么这样调用”，不写情绪描写、不写最终回复
- 不要输出无关解释
- 请及时结束本次任务:可以调用stop_thinking或不输出功能调用结束本次任务
- 每行一个调用
- 调用直接写在正文里，不要用代码块包裹
- 禁止使用 XML / DSML 等其它调用格式
- 无参数功能写()
- 字符串用双引号
- 字符串内容需要换行时优先写 \\n；直接换行也能被解析

输出格式：
我该怎么做的思考内容。

功能名(参数名=参数值, ...)
功能名(参数名=参数值, ...)"""

    async def thinking(
        self,
        args: ChatArgs,
        current_history: History,
        history_list: list[History],
        image_history: str,
        fallback_history: list[str],
        chat_message: ChatMessage,
        sender,
        initiative: bool = False,
    ):
        """跑一轮完整思考。

        :param initiative: 主动开口模式。此时 `current_history` 是**合成的**、
            不在历史队列里，`args.user_id` 也是占位——这样既有完整上下文，
            又不会污染对话历史（见 `initiative_start`）。
        """
        user_info = get_user_info(args.user_id)
        prompt = self.get_system_prompt() + "\n\n"
        prompt += await self.get_prompt(
            args.chat_id,
            args.user_id,
            user_info,
            args.bot,
            current_history,
            sender,
            initiative=initiative,
        )
        prompt += await self.get_history_prompt(
            args.bot,
            history_list,
            image_history,
            fallback_history,
        )
        message = LLMMessage(role="user", content=LLMContents().text(prompt))
        self.messages.add_message(message)
        started = time.perf_counter()
        thinking_times = 0
        limit = config.get("max_thinking_times")
        failed = False
        try:
            while thinking_times <= limit:
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
                if llm_manager.has_type(ModelType.ATRI):
                    m_type = ModelType.ATRI
                else:
                    m_type = ModelType.CHAT
                resp = await llm_manager.call_model_by_type(
                    m_type, self.messages, "主Agent思考"
                )
                _, functions_data, issues = await self.process_resp(resp.content)
                if not functions_data and not issues:
                    break
                back = await self.do_action(
                    functions_data,
                    args.user_id,
                    args.chat_id,
                    sender,
                    current_history,
                    issues,
                )
                self.messages.add_message(
                    LLMMessage("assistant", LLMContents().text(resp.content))
                )
                thinking_times += 1
                msg = "\n".join(back)
                if thinking_times == limit:
                    msg += "\n调用次数达到上限,本次调用结束后直接结束本次任务"
                self.messages.add_message(LLMMessage("user", LLMContents().text(msg)))
        except StopThinkingError:
            pass
        except Exception:
            failed = True
            raise
        finally:
            try:
                settled = settle_from_turn(
                    replied=bool(getattr(sender, "sent_count", 0)),
                    rounds=thinking_times,
                    user_was_master=(get_user_group(args.user_id) == "主人"),
                )
                telemetry.feel(
                    mood=settled.mood,
                    irritation=settled.irritation,
                    source="turn_settle",
                )
                if getattr(sender, "sent_count", 0):
                    bump_familiarity(args.user_id)
            except Exception:
                pass
            telemetry.turn(
                chat_id=str(args.chat_id),
                rounds=thinking_times,
                duration=time.perf_counter() - started,
                hit_limit=thinking_times >= limit,
                failed=failed,
            )

    @staticmethod
    def parse_dsl(text: str) -> list[dict[str, Any]]:
        """把 DSL 文本解析成调用列表；具体解析与容错见 action_parser。"""
        return parse_calls(text)

    @classmethod
    async def process_resp(cls, resp) -> tuple[str, list, list[str]]:
        """把模型输出拆成（正文, 调用列表, 解析问题）；解析失败不再抛出。"""
        return split_response(resp if isinstance(resp, str) else "")

    @staticmethod
    async def do_action(
        functions_data: list,
        user_id,
        chat_id,
        sender,
        current_history: History,
        issues: list[str] | None = None,
    ) -> list:
        calling_back = []
        if issues:
            # 解析层的问题先回填：模型据此重写，而不是整轮静默失败
            detail = "；".join(issues)
            calling_back.append(
                f"本次输出有 {len(issues)} 处调用无法解析：{detail}。"
                "请严格按「功能名(参数名=参数值, ...)」格式重写：字符串用双引号，"
                "需要换行写 \\n，每个调用独占一行，不要用代码块包裹"
            )
        for func in functions_data:
            func_name = func["function"]
            func_cls = FunctionCallingManager.function_calling.get(func_name)
            if func_cls is None:
                log.debug(f"未知的调用功能:{func_name}，已跳过")
                known = "、".join(FunctionCallingManager.function_calling)
                calling_back.append(f"未知的调用功能:{func_name}，可用功能:{known}")
                continue
            # 非 stop_thinking 的不可用功能，区分"环境/配置不可用"与"模型幻觉"
            if not FunctionCallingManager.is_available(func_name, sender, user_id):
                check = FunctionCallingManager.get_can_enable(func_name)
                reason = check.reason or "该功能已被关闭"
                log.debug(f"不可用的调用功能:{func_name}（{reason}），已跳过")
                calling_back.append(f"功能 {func_name} 当前不可用：{reason}")
                continue
            # 不常用功能只能由 call 调用：直接写名字给了更明确的重写提示
            if FunctionCallingManager.is_rare(func_name):
                log.debug(f"不常用功能被直接调用:{func_name}，已引导改用 call")
                calling_back.append(
                    f"功能 {func_name} 是不常用功能，不能直接调用。"
                    f'请改用 call(function="{func_name}", args={{...}})'
                )
                continue
            # 按功能声明校验参数名与类型：问题直接回填给模型，
            # 不必等 call() 抛 TypeError 才暴露（那样模型只看到「调用失败」）
            args, arg_issues = normalize_args(func_cls, func["args"])
            if arg_issues:
                log.debug(f"功能 {func_name} 参数不合规:{arg_issues}")
                detail = "；".join(arg_issues)
                calling_back.append(
                    f"功能 {func_name} 的参数不合规：{detail}。"
                    "请对照上面的参数声明重写本次调用"
                )
                continue
            tool_started = time.perf_counter()
            try:
                # 使用 FunctionCallingManager 统一处理功能调用
                calling_data = FunctionCallingData(
                    user_id, chat_id, sender, args, current_history
                )
                log.debug(f"调用功能 {func_name}")
                result = await FunctionCallingManager.call(func_name, calling_data)
                calling_back.append(f"调用{func_name}成功,返回:{result}")
                telemetry.tool(
                    name=func_name,
                    ok=True,
                    duration=time.perf_counter() - tool_started,
                )
            except StopThinkingError:
                telemetry.tool(
                    name=func_name,
                    ok=True,
                    duration=time.perf_counter() - tool_started,
                )
                raise
            except Exception as e:
                log.warning(
                    f"用户 {user_id} 功能调用失败 {func_name}:\n{str_traceback(e)}"
                )
                calling_back.append(f"调用{func_name}失败,请勿再次调用")
                telemetry.tool(
                    name=func_name,
                    ok=False,
                    duration=time.perf_counter() - tool_started,
                    error=str(e),
                )
        return calling_back
