from asyncio import Lock
from time import monotonic

from nonebot.adapters.onebot.v11 import Message

from ATRI.log import log
from ATRI.utils.datetime import now

from .initiative import initiative_limiter
from .model import ChatArgs
from .model_health import check_agent_model_health
from .sender import ChatSender, OneBotChatSender, QQChatSender
from .telemetry import telemetry
from .temp_his import ChatMessage, TempHisManager

#: 缺失必需模型时，同一进程 10 分钟内只提醒一次，避免每个群每条消息刷屏。
_MISSING_MODEL_NOTICE_INTERVAL = 600.0
_last_missing_model_notice = 0.0
_last_empty_chat_notice = 0.0


def _check_initiative(chat_id: str) -> tuple[bool, str]:
    """主动发言限流检查。状态读失败时**放行**——不能让状态模块的小故障
    变成「她永远不说话」。"""
    try:
        from .state import get_state

        irritation = get_state().irritation
    except Exception:
        irritation = 0.0
    return initiative_limiter.allow(chat_id, irritation=irritation)


class ATRIAgent:
    waiting_num = 0
    chat_lock = Lock()

    @classmethod
    async def chat(
        cls, chat_sender: ChatSender, message: Message | str, args: ChatArgs
    ):
        chat_message = await TempHisManager.add_message(message, args)
        if chat_message.message is None:
            return
        if args.skip_chat:
            return
        if cls.waiting_num and not args.skip_judgment:
            return
        cls.waiting_num += 1
        try:
            async with cls.chat_lock:
                if not chat_message.force_chat and not chat_message.check_time():
                    return
                await cls._chat(chat_sender, message, args, chat_message)
        finally:
            cls.waiting_num -= 1

    @classmethod
    async def _chat(
        cls,
        chat_sender: ChatSender,
        message: Message | str,
        args: ChatArgs,
        chat_message: ChatMessage,
    ):
        # 局部导入：brain 下的模块会反向导入 agent 包，放在模块顶层会形成导入环
        from ..brain import JudgmentModel, ThinkingModel

        this_msg = chat_message.message
        history_list = chat_message.history[:-1]
        if args.skip_chat:
            return
        global _last_missing_model_notice, _last_empty_chat_notice
        health = check_agent_model_health()
        # 只有思考模型缺失才必须拒绝：没有 atri-think 就没法决策。
        # atri-chat 缺失时链路会自动降级（say/md 用 intent 直发、face 丢弃），
        # 因此不该整轮拒绝——否则「只配了思考模型」的部署会完全不说话。
        think_missing = [
            item for item in health if item.type_key == "atri-think" and not item.ok
        ]
        chat_missing = [
            item for item in health if item.type_key == "atri-chat" and not item.ok
        ]
        now_mono = monotonic()
        if chat_missing and (
            now_mono - _last_empty_chat_notice > _MISSING_MODEL_NOTICE_INTERVAL
        ):
            _last_empty_chat_notice = now_mono
            names = "、".join(f"{item.label}({item.type_key})" for item in chat_missing)
            log.warning(
                f"Agent 缺少 {names}，聊天将降级为意图直发；"
                "如需正常措辞请在 WebUI 的「AI模型设置」中配置对应类型。"
            )
        if think_missing:
            if now_mono - _last_missing_model_notice > _MISSING_MODEL_NOTICE_INTERVAL:
                _last_missing_model_notice = now_mono
                names = "、".join(
                    f"{item.label}({item.type_key})" for item in think_missing
                )
                log.error(
                    f"Agent 缺少必需模型：{names}，已拒绝本次聊天。"
                    "请在 WebUI 的「AI模型设置」中配置对应类型。"
                )
                try:
                    await chat_sender.send("我暂时没法回复：缺少必需的模型配置。")
                except Exception as exc:
                    log.debug(f"发送缺少模型提示失败：{exc}")
            return
        if isinstance(message, str):
            plain_text = message
        else:
            plain_text = message.extract_plain_text()
        now_time = now().time()
        if not args.skip_judgment and (plain_text == "" or 2 <= now_time.hour < 6):
            log.info("跳过主动聊天")
            return
        proactive = not args.skip_judgment
        if proactive:
            allowed, reason = _check_initiative(args.chat_id)
            telemetry.initiative(
                chat_id=str(args.chat_id), allowed=allowed, reason=reason
            )
            if not allowed:
                log.debug(f"不主动发言：{reason}")
                return
        if not args.skip_judgment and not await JudgmentModel.analyze(
            args.bot, this_msg, history_list, chat_message
        ):
            return
        await ThinkingModel().thinking(
            args,
            this_msg,
            history_list,
            chat_message.image_history,
            chat_message.fallback_history,
            chat_message,
            chat_sender,
        )
        if proactive and getattr(chat_sender, "sent_count", 0) > 0:
            initiative_limiter.record(args.chat_id)
        if isinstance(chat_sender, QQChatSender | OneBotChatSender):
            await chat_sender.finish()
