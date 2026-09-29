from asyncio import Lock

from nonebot.adapters.onebot.v11 import Message

from ATRI.log import log
from ATRI.utils.datetime import now

from ..brain import (
    ActionModel,
    JudgmentModel,
    ReplyModel,
    SensoryAnalyzer,
    ThinkingModel,
)
from ..llm import ModelType, llm_manager
from .history import get_history_messages
from .model import ChatArgs
from .sender import ChatSender
from .temp_his import ChatMessage, TempHisManager


class ATRIAgent:
    waiting_num = 0
    chat_lock = Lock()

    @classmethod
    async def chat(cls, chat_sender: ChatSender, message: Message, args: ChatArgs):
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
                TempHisManager.start_processing(chat_message)
                try:
                    await cls._chat(chat_sender, message, args, chat_message)
                finally:
                    TempHisManager.stop_processing(chat_message)
        finally:
            cls.waiting_num -= 1

    @classmethod
    async def _chat(
        cls,
        chat_sender: ChatSender,
        message: Message,
        args: ChatArgs,
        chat_message: ChatMessage,
    ):
        chat_id = args.chat_id
        user_id = args.user_id
        this_msg = chat_message.message
        history_list = chat_message.history[:-1]
        no_tool_model = False
        if args.skip_chat:
            return
        if not llm_manager.has_type(ModelType.CHAT):
            log.warning("没有配置chat类型的模型")
            return
        if not llm_manager.has_type(ModelType.TOOL):
            if not args.skip_judgment:
                return
            no_tool_model = True
        plain_text = message.extract_plain_text()
        now_time = now().time()
        if not args.skip_judgment and (plain_text == "" or 2 <= now_time.hour < 6):
            return
        msg = await this_msg.get_message(args.bot)
        messages = await get_history_messages(
            args.bot, history_list, chat_message.fallback_history
        )
        msg_his = "\n".join(messages) if messages else "无历史聊天记录"
        img_his = chat_message.image_history
        if not no_tool_model:
            try:
                sensory = await SensoryAnalyzer.analyze(f"{img_his}\n\n{msg_his}", msg)
                str_sensory = (
                    f"整体情感:{sensory.get('sensory', {}).get('total', '未知')} "
                    f"强度:{sensory.get('sensory', {}).get('strength', '未知')} "
                    f"具体情感:{','.join(sensory.get('sensory', {}).get('tag', ['未知']))}\n"
                    f"当前对话主题:{sensory.get('theme', '未知')} "
                    f"对方需求:{','.join(sensory.get('demand', ['未知']))}"
                )
                log.info(f"情感分析:{str_sensory}")
            except Exception:
                str_sensory = None
            if not args.skip_judgment and not await JudgmentModel.analyze(
                f"{img_his}\n\n{msg_his}", msg, str_sensory
            ):
                return
        else:
            str_sensory = None
        thinking, functions_data = await ThinkingModel.thinking(
            args.bot,
            chat_id,
            user_id,
            str_sensory,
            this_msg,
            history_list,
            chat_message.image_history,
            chat_message.fallback_history,
            chat_message,
        )
        thinking_list = [thinking]
        times = 0
        calling_backs = []
        while True:
            times += 1
            continue_chat, calling_back = await ActionModel.do_action(
                functions_data, user_id, chat_id
            )
            calling_backs += calling_back
            if continue_chat:
                from ..config import config

                thinking, functions_data = await ThinkingModel.continue_thinking(
                    args.bot,
                    chat_id,
                    user_id,
                    calling_backs,
                    times >= config.max_thinking_times,
                    this_msg,
                    chat_message,
                )
                thinking_list.append(thinking)
            else:
                break
        await ReplyModel.reply(
            args.bot,
            chat_id,
            user_id,
            thinking_list,
            chat_sender,
            this_msg,
            with_tts=args.skip_judgment,
        )
