import asyncio
import time
from typing import Any

from ATRI.exceptions import str_traceback
from ATRI.utils.datetime import now

from ..agent.action_executor import ActionExecutor
from ..agent.explanations import get_top_explanations
from ..agent.function_calling import FunctionCallingData, FunctionCallingManager
from ..agent.function_permission import PermissionContext
from ..agent.history import History, get_history_messages
from ..agent.life import get_life_system
from ..agent.memory.persona import render_memories, render_self_memories
from ..agent.model import ChatArgs
from ..agent.outbox import ChatOutbox
from ..agent.role import ATRI
from ..agent.schedule import ATRISchedule
from ..agent.skills import skill_manager
from ..agent.state import get_state, settle_from_turn, state_prompt, state_store
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
from .action_parser import parse_calls, split_response


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


def _burst_count(history_list: list[History], user_id: str) -> int:
    """同一个人从队尾起连着发了几条：烦躁度的真实输入来源。"""
    uid = str(user_id or "")
    if not uid:
        return 0
    count = 0
    for node in reversed(history_list or []):
        sender = getattr(node, "sender", None)
        if sender is None or str(sender) != uid:
            break
        count += 1
    return count


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
    """把内心状态渲染成提示词片段（与插话判断同一份）。"""
    text = state_prompt()
    if not text:
        return ""
    return f"#你此刻的状态\n{text}\n"


#: 模型的正文被当成「想说但忘了发」时，回填给它的话。
FORGOT_TO_SEND_HINT = (
    "你刚才写的正文不会发送给任何人，只有调用 say 才会真的说出来。"
    '如果那是想说的话，请调用 say(intent="...") 重新发一次；'
    "如果只是思考，这一轮不要再输出任何功能调用即可结束。"
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
    already_sent: bool = False,
) -> bool:
    """这一轮要不要提醒模型「你忘了调用 say」。

    ``already_sent``：本轮已经真的发出过消息时不再提醒——没有功能调用的
    收尾轮是正常结束，不该被当成「忘了发」。没有它的话，每次发言后的
    收尾轮都会被追问两遍，白烧 token 还可能让她重复说话。
    """
    if functions_data or issues or already_sent:
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
            'say(intent="想表达的意思", tone="语气")\n'
            'md(intent="这张卡片要说明什么", image="")\n'
            'face(intent="想表达的情绪")\n'
            "...\n"
            "# 功能调用返回了数据，你会看到结果并继续思考；"
            "没有返回数据的调用（例如发送消息）只会显示「调用完成」，"
            "本次思考随即结束。\n"
            "想继续说或继续做事，就调用会返回数据的功能；"
            "不需要继续时，调用完不需要结果的功能、或不再输出任何功能调用即可。\n"
            "注意：正文不会发出去，想说话必须调用 say。"
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
        permission: PermissionContext | None = None,
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
            FunctionCallingManager.get_available(sender, user_id, permission),
            FunctionCallingManager.hidden_available(sender, user_id, permission),
        )
        # 技能目录 + 内嵌技能正文
        prompt += skill_manager.get_prompt_block(
            int(config.get("skill_prompt_budget") or 10000)
        )
        # 生活系统：位置 / 日程 / 穿搭 / 家
        try:
            life = get_life_system()
            parts = life.prompt_parts()
            prompt += f"#你现在在哪里\n{parts['location']}\n\n"
            if parts["schedule"]:
                prompt += f"#今天的安排\n{parts['schedule']}\n\n"
            else:
                prompt += (
                    "#今天的安排\n今天还没有安排。"
                    '想安排的话，调用 plan(action="fill", hint="...")。\n\n'
                )
            prompt += f"#今日穿搭\n{parts['outfit']}\n\n"
            if parts["home"]:
                prompt += f"#你的家\n{parts['home']}\n\n"

        except Exception as exc:
            log.warning(f"生活提示词生成失败，已跳过：{exc}")
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
                "要不要为此说点什么、说什么，由你决定；不合适就什么都不调用，结束本次思考。\n"
            )
            people = await _describe_addressee(bot, chat_id, user_id, user_info)
            if people:
                prompt += people
        elif mode == "initiative":
            prompt += (
                "#你现在的情况\n"
                "这个群现在没有人在跟你说话。\n"
                f"{lst_msg}\n"
                "你可以选择主动说点什么，也可以什么都不说（本轮不调用任何功能即可结束）。\n"
            )
        elif mode == "life":
            prompt += (
                "#新的一天\n"
                f"{lst_msg}\n"
                "现在是早上，你可以先看看今天想怎么过：要出门就安排前往与回到，"
                "想待在家就写清活动。决定好后调用 "
                'plan(action="fill", hint="今天的打算")，再决定要不要说早安。\n'
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
                "想说什么就调用 say；觉得现在说这个不合适，就不要再调用任何功能。\n\n"
            )
        elif mode == "initiative":
            prompt += (
                f"现在时间是{now_text}，没有人 @ 你，也没有可以回应的消息。\n"
                "如果你想说话，就直接调用 say 把话说出来；"
                "如果此刻没什么想说的，本轮不要调用任何功能。\n\n"
            )
        elif mode == "life":
            # 早间计划同样是「无人触发」：不能走 chat 分支，那条分支会拼出
            # 「XX[None]的消息引起了你的注意」，而这里根本没有对话人。
            prompt += (
                f"现在是{now_text}，没有人叫你，也没有需要回应的消息。\n"
                "这是新的一天开始，先安排今天怎么过；安排完再决定要不要说早安，"
                "不想说就不调用任何功能。\n\n"
            )
        else:
            prompt += f"现在{await get_name(bot, lst_history.sender, chat_id)}[id:{user_id}]在{lst_history.time}的消息引起了你的注意\n"
            prompt += f"消息内容:{lst_msg if lst_history else ATRI.role_name}\n\n"
        return prompt

    @staticmethod
    def get_function_prompt(
        available: dict[str, type] | None = None,
        hidden: dict[str, type] | None = None,
    ):
        if available is None:
            available = FunctionCallingManager.get_available()
        if hidden is None:
            hidden = FunctionCallingManager.hidden_available()
        body = "\n".join(
            f"""{f.function_name}:
    说明: {f.description}{f"\n    参数:\n{'\n'.join(f'        -{arg.name} {arg.type}: {arg.description}' for arg in f.args)}" if f.args else ""}"""
            for f in available.values()
        )
        extras = ""
        if hidden:
            extras = (
                "\n\n以下功能不进常规清单，但允许直接调用（按需使用）：\n"
                + "\n".join(
                    f"- {f.function_name}：{f.description}" for f in hidden.values()
                )
            )
        return f"""你可以调用以下功能，禁止调用未列出的功能:
{body}{extras}

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
- 要说话就调用 say；怎么说话写在它的参数说明里，这里不再重复
- 需要逐字发送代码/命令/引用原文时用 say_exact；普通聊天不要用它
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
        permission = PermissionContext.from_chat_args(args)
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
            permission=permission,
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
        # Chat 渲染需要的不可变上下文快照：角色 / 状态 / 关系 / 最近对话
        from .chat_model import build_context

        render_ctx = build_context(
            role_name=ATRI.role_name,
            personality=ATRI.personality,
            state=get_state_prompt().strip(),
            relation=describe_relation(user_info),
            history=await get_history_messages(
                args.bot, history_list, fallback_history
            ),
            chat_id=str(args.chat_id),
        )
        outbox = ChatOutbox(
            ctx=render_ctx,
            inter_message_delay=float(config.get("reply_inter_message_delay") or 0.4),
            parallel_render=bool(config.get("chat_parallel_render")),
            max_parallel_render=int(config.get("max_parallel_render") or 2),
        )
        executor = ActionExecutor(
            outbox=outbox,
            max_side_effects=int(config.get("max_side_effects_per_turn") or 5),
        )
        action_ctx = FunctionCallingData(
            args.user_id,
            args.chat_id,
            sender,
            {},
            current_history,
            bot=args.bot,
            permission=permission,
        )
        try:
            while thinking_times <= limit:
                timely_messages_prompt = await chat_message.get_timely_messages_prompt(
                    args.bot
                )
                if timely_messages_prompt:
                    self.messages.add_message(
                        LLMMessage(
                            "user",
                            LLMContents().text(timely_messages_prompt),
                        )
                    )
                resp = await llm_manager.call_agent(
                    ModelType.ATRI_THINK, self.messages, "主Agent思考"
                )
                _, functions_data, issues = await self.process_resp(resp.content)
                if not functions_data and not issues:
                    # 有正文、没调用：这句话发不出去。提醒模型补一次 say，
                    # 否则聊天里就是「她突然不理人」，而且日志里看不出任何异常。
                    content = str(getattr(resp, "content", "") or "").strip()
                    if should_remind_forgot_send(
                        content,
                        functions_data,
                        issues,
                        forgot_send,
                        already_sent=bool(getattr(sender, "sent_count", 0))
                        or outbox.submitted_count > 0,
                    ):
                        forgot_send += 1
                        log.warning(
                            f"第 {thinking_times + 1} 轮只有正文没有调用，"
                            f"已提醒模型改用 say（{len(content)} 字）"
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
                result = await executor.run(functions_data, action_ctx, issues)
                self.messages.add_message(
                    LLMMessage("assistant", LLMContents().text(resp.content))
                )
                thinking_times += 1
                # 发送失败的旁路反馈：不挤进本轮决策，只让下一轮知道
                for note in outbox.poll_feedback():
                    self.messages.add_message(
                        LLMMessage("user", LLMContents().text(note))
                    )
                if not result.keep_thinking:
                    # 本轮只做了「调用完成」这类无返回数据的动作（如发送消息），
                    # 没有新信息需要消化，直接结束，不再多烧一轮 token。
                    break
                msg = "\n".join(result.feedback)
                if thinking_times == limit:
                    msg += "\n调用次数达到上限,本次调用结束后直接结束本次任务"
                self.messages.add_message(LLMMessage("user", LLMContents().text(msg)))
        except Exception:
            failed = True
            raise
        finally:
            drain_cancelled: asyncio.CancelledError | None = None
            # 回合结束前排空队列：消息真的发出、sent_count 与历史落定。
            try:
                await outbox.drain(timeout=30.0)
            except asyncio.CancelledError as exc:
                drain_cancelled = exc
            except Exception as exc:
                log.warning(f"排空聊天队列失败:{str_traceback(exc)}")
            try:
                settled = settle_from_turn(
                    replied=bool(getattr(sender, "sent_count", 0)),
                    rounds=thinking_times,
                    user_was_master=(get_user_group(args.user_id) == "主人"),
                    burst=_burst_count(history_list, args.user_id),
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
            if drain_cancelled is not None:
                raise drain_cancelled

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
        permission: PermissionContext | None = None,
    ) -> tuple[list[str], bool]:
        """执行本轮功能调用，返回（回填给模型的内容, 是否继续思考）。

        这是 ``ActionExecutor`` 的兼容入口：不带 Outbox 时副作用按模型输出
        顺序直接执行，数据调用仍按 ``parallel_safe`` 并发/串行。
        """
        from ..agent.action_executor import ActionExecutor

        ctx = FunctionCallingData(
            user_id,
            chat_id,
            sender,
            {},
            current_history,
            bot=bot,
            permission=permission,
        )
        result = await ActionExecutor().run(functions_data, ctx, issues)
        return result.feedback, result.keep_thinking
