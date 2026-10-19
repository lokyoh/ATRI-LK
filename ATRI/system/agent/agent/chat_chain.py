from asyncio import Lock

from nonebot.adapters.onebot.v11 import Message

from ATRI.log import log
from ATRI.utils.datetime import now

from ..llm import ModelType, llm_manager
from .model import ChatArgs
from .sender import ChatSender, OneBotChatSender, QQChatSender
from .temp_his import ChatMessage, TempHisManager


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
        if not llm_manager.has_type(ModelType.CHAT):
            log.warning("没有配置chat类型的模型")
            return
        if isinstance(message, str):
            plain_text = message
        else:
            plain_text = message.extract_plain_text()
        now_time = now().time()
        if not args.skip_judgment and (plain_text == "" or 2 <= now_time.hour < 6):
            log.info("跳过主动聊天")
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
        if isinstance(chat_sender, (QQChatSender, OneBotChatSender)):
            await chat_sender.finish()
