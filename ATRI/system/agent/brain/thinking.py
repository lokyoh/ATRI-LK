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


async def _describe_addressee(bot, chat_id, user_id, user_info) -> str:
    """渲染「这次是对谁说话」——身份、关系、关于他的记忆。

    抽出成函数是因为两个 mode 都要用：
    - ``chat``：正在跟她说话的人
    - ``scheduled``：定时任务的委托人（她答应过要提醒的那个人）

    定时任务原本没有这一段，于是她不知道话该对谁说，
    只能对着空上下文泛泛而谈。
    """
    # 占位用户没有真实档案，不渲染（主动开口就是这种情况）
    if str(user_id).startswith("__"):
        return ""
    try:
        user_name = await get_name(bot, user_id, chat_id)
    except Exception:
        return ""
    if not user_name:
        return ""

    text = f"#{user_name}信息:\n"
    import contextlib

    is_master = False
    with contextlib.suppress(Exception):
        is_master = get_user_group(user_id) == "主人"
    text += (
        f"{user_name}是你的主人,主人就是亚托莉的一切,主人的一切要求都要满足\n"
        if is_master
        else f"{user_name}只是普通用户，请以普通的群友对待，问题需要谨慎地回复\n"
    )
    text += f"你和{user_name}的关系：{describe_relation(user_info)}\n"

    if user_info.memory:
        # 渲染成回忆的口吻，而不是把 Python 列表原样拼进提示词。
        memory_text = render_memories(user_name, user_info.memory)
        if memory_text:
            text += f"#你记得的事\n{memory_text}\n"

    text += (
        f"{user_name}用户画像：" + user_info.profile
        if user_info.profile
        else "暂时没有用户画像。\n"
    )
    return text


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


#: 模型的正文被当成「想说但忘了发」时，回填给它的话。
FORGOT_TO_SEND_HINT = (
    "你刚才写的正文不会发送给任何人，只有调用 send_message 才会真的说出来。"
    '如果那是想说的话，请调用 send_message(msg="...") 重新发一次；'
    "如果只是思考，请调用 stop_thinking() 结束。"
)

#: 连续几次「只写正文不调用」就放弃。已提醒过仍不改，再循环只是白烧 token。
MAX_FORGOT_SEND = 2


def looks_like_reply(text: str) -> bool:
    """正文像不像「本来想发出去的话」。"""
    body = (text or "").strip()
    if not body:
        return False
    if not any(ch.isalnum() for ch in body):
        return False
    for prefix in ("思考", "分析", "说明", "注：", "（", "(", "#"):
        if body.startswith(prefix):
            return False
    return True


def should_remind_forgot_send(
    content: str,
    functions_data: list,
    issues: list,
    reminded: int,
) -> bool:
    """这一轮要不要提醒模型「你忘了调用 send_message」。"""
    if functions_data or issues:
        return False
    if reminded >= MAX_FORGOT_SEND:
        return False
    return looks_like_reply(content)


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
        return (
            f"{image_history}\n\n{history_prompt}\n#历史聊天记录结束\n\n"
            "# 现在轮到你行动，你与外界交互只能通过功能调用实现\n"
            "输出格式：\n"
            "我该怎么做的思考内容。\n"
            'send_message(msg="想要说的话")\n'
            "...\n"
            "# 不需要调用的结果或不想说就在最后调用 stop_thinking 结束思考。\n"
            "stop_thinking()"
        )

    @classmethod
    async def get_prompt(
        cls,
        chat_id,
        user_id,
        user_info,
        bot,
        lst_history: History,
        sender=None,
        mode: str = "chat",
    ):
        """拼装思考提示词。

        :param mode: 触发语境，见 `thinking` 的说明。
            非 ``chat`` 时没有对话人、没有当前消息，
            但其余上下文（角色、技能、日程、状态、自我记忆、工具）全部保留——
            这正是「用全量 thinking 来开口」与「用轻量提示词开口」的区别：
            她有完整的自我认知和工具能力，只是这次不是被人叫到。
        """
        lst_msg = await lst_history.message.get_message(bot) if lst_history else ""
        prompt = cls.get_function_prompt(
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
        if mode == "scheduled":
            prompt += (
                "#你现在的任务\n"
                "现在到时间了，这件是你之前给自己定下的提醒：\n"
                f"{lst_msg}\n"
                "要不要为此说点什么、说什么，由你决定；不合适就 stop_thinking。\n"
            )
            people = await _describe_addressee(bot, chat_id, user_id, user_info)
            if people:
                prompt += people
        elif mode == "initiative":
            prompt += (
                "#你现在的情况\n"
                "这个群现在没有人在跟你说话。\n"
                f"{lst_msg}\n"
                "你可以选择主动说点什么，也可以什么都不说（直接调用 stop_thinking）。\n"
            )
        else:
            # 对话人信息
            prompt += await _describe_addressee(bot, chat_id, user_id, user_info)
        # 她自己的经历：这是「独立个体」在提示词里的落点。
        # 与「关于用户的事」分开渲染——前者可以自然流露，后者要看场合。
        # **主动开口时这条更重要**：她的素材主要来自这里。
        self_memories = get_self_memories()
        if self_memories:
            self_text = render_self_memories(self_memories)
            if self_text:
                prompt += f"\n{self_text}\n"
        # 对话中词语解释（只对真实消息有意义）
        exp = get_top_explanations(lst_msg) if lst_msg else []
        if exp:
            prompt += "\n其中词语解释:\n"
            prompt += "\n".join(exp)
            prompt += "\n"
        # 对话提示信息
        prompt += "\n"
        now_text = now().strftime("%Y年%m月%d日%a-%H时%M分")
        if mode == "scheduled":
            prompt += (
                f"现在是{now_text}。\n"
                "这不需要有人叫你——时间到了，你自己定的提醒该兑现了。\n"
                "想说什么就调用 send_message；觉得现在说这个不合适，调用 stop_thinking。\n\n"
            )
        elif mode == "initiative":
            prompt += (
                f"现在时间是{now_text}，没有人 @ 你，也没有可以回应的消息。\n"
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
    def get_system_prompt(role_prompt: str = ""):
        head = "你是项目ATRI-LK聊天机器人的大脑：根据输入、上下文与各种信息做出决策，生成功能调用，读取调用反馈后继续，直到结束本次任务。\n"
        if role_prompt:
            head += f"\n#你的信息\n{role_prompt}\n"
        return (
            head
            + """
要求：
- 要说话就调用 send_message；怎么说话写在它的参数说明里，这里不再重复
- 调用之前可以写一两句"为什么这样调用"，但不要写情绪描写、不要写最终回复、不要写无关解释
- 每行一个调用，直接写在正文里，不要用代码块包裹
- 禁止使用 XML / DSML 等其它调用格式
- 无参数功能写()；字符串用双引号
- 字符串内容需要换行时优先写 \\n；直接换行也能被解析
"""
        )

    async def thinking(
        self,
        args: ChatArgs,
        current_history: History,
        history_list: list[History],
        image_history: str,
        fallback_history: list[str],
        chat_message: ChatMessage,
        sender,
        mode: str = "chat",
    ):
        """跑一轮完整思考。

        :param mode: 这次思考由什么触发。三种语境差别很大，
            不能共用一个布尔标志——之前定时任务复用了「主动开口」，
            提示词里就出现了「这个群没有人在跟你说话」「没有人 @ 你」
            这类主动聊天语气，而它其实是「到点了该做某件事」。

            - ``chat``：有人在跟她说话（默认）
            - ``initiative``：她自己想开口（无人触发，主动搭话）
            - ``scheduled``：定时任务到点（她之前给自己定的提醒）

            后两种的 `current_history` 是**合成的**、不在历史队列里，
            `args.user_id` 也是占位——这样既有完整上下文，
            又不会污染对话历史（见 `initiative_start`）。
        """
        user_info = get_user_info(args.user_id)
        prompt = self.get_system_prompt(ATRI.get_role_prompt()) + "\n\n"
        prompt += await self.get_prompt(
            args.chat_id,
            args.user_id,
            user_info,
            args.bot,
            current_history,
            sender,
            mode=mode,
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
        forgot_send = 0
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
                    # 有正文、没调用：这句话发不出去。提醒模型补一次 send_message，
                    # 否则聊天里就是「她突然不理人」，而且日志里看不出任何异常。
                    content = str(getattr(resp, "content", "") or "").strip()
                    if should_remind_forgot_send(
                        content, functions_data, issues, forgot_send
                    ):
                        forgot_send += 1
                        log.warning(
                            f"第 {thinking_times + 1} 轮只有正文没有调用，"
                            f"已提醒模型改用 send_message（{len(content)} 字）"
                        )
                        telemetry.record(
                            "forgot_send",
                            chat_id=str(args.chat_id),
                            mode=mode,
                            length=len(content),
                            attempt=forgot_send,
                        )
                        self.messages.add_message(
                            LLMMessage("assistant", LLMContents().text(content))
                        )
                        self.messages.add_message(
                            LLMMessage("user", LLMContents().text(FORGOT_TO_SEND_HINT))
                        )
                        thinking_times += 1
                        continue
                    break
                back = await self.do_action(
                    functions_data,
                    args.user_id,
                    args.chat_id,
                    sender,
                    current_history,
                    issues,
                    bot=args.bot,
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
                # 非 chat 的 trace 必须能和正常回复区分开。
                # 之前漏传这个字段，导致真实数据里 proactive 全是 0，
                # 「主动开口花了多少轮/多久」根本查不出来。
                proactive=mode != "chat",
                mode=mode,
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
        bot=None,
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
                    user_id, chat_id, sender, args, current_history, bot=bot
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
