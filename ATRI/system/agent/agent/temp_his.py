import time
from asyncio import Future, Lock, gather, get_running_loop
from typing import ClassVar

from ..config import config
from .history import ChatHistoryManager, History, history_logger
from .model import ChatArgs


class ChatMessage:
    def __init__(
        self,
        message: History | None,
        history: list[History],
        image_history: str,
        fallback_history: list[str],
        args: ChatArgs,
    ):
        self.message = message
        self.history = history
        self.image_history = image_history
        self.fallback_history = fallback_history
        self.messages_during_processing: list[Future[History | None]] = []
        self.user_id = args.user_id
        self.force_chat = args.is_force_chat()
        self.time_stamp = time.time()

    def check_time(self) -> bool:
        return time.time() - self.time_stamp <= 180

    async def get_processing_messages_prompt(self, bot) -> str:
        pending_messages = list(self.messages_during_processing)
        histories = await gather(*pending_messages) if pending_messages else []
        messages = [
            await history.get_message(bot)
            for history in histories
            if history is not None
        ]
        content = "\n".join(messages) if messages else "本次处理期间暂无新消息。"
        return (
            "#本次处理期间收到的新消息\n"
            "以下消息是在当前任务开始处理后到达的，按接收顺序排列；"
            "这些消息仍会分别保留在聊天历史中。\n"
            f"{content}\n"
        )


class TempHisManager:
    temp_lock: ClassVar[dict[str, Lock]] = {}
    processing_messages: ClassVar[dict[str, ChatMessage]] = {}

    @classmethod
    async def add_message(cls, message, args: ChatArgs) -> ChatMessage:
        if args.chat_id not in cls.temp_lock:
            cls.temp_lock[args.chat_id] = Lock()
        async with cls.temp_lock[args.chat_id]:
            active_message = cls.processing_messages.get(args.chat_id)
            pending_history = (
                get_running_loop().create_future() if active_message else None
            )
            if active_message and pending_history:
                active_message.messages_during_processing.append(pending_history)
            try:
                history = await History.create(
                    args.bot.self_id, args.user_id, args.chat_id, message
                )
            except BaseException:
                if pending_history and not pending_history.done():
                    pending_history.set_result(None)
                raise
            if pending_history:
                pending_history.set_result(history)
            if history is None:
                return ChatMessage(None, [], "", [], args)
            chat_history = ChatHistoryManager.get_history(args.chat_id)
            if not chat_history.fallback_history_loaded:
                if not chat_history.get_history():
                    chat_history.fallback_history = history_logger.get_recent_messages(
                        args.chat_id, config.max_history
                    )
                chat_history.fallback_history_loaded = True
            await chat_history.add_history(
                args.bot.self_id, args.user_id, args.chat_id, history
            )
            return ChatMessage(
                history,
                chat_history.get_history(),
                ChatHistoryManager.get_img_history(args.chat_id).get_history(),
                chat_history.fallback_history,
                args,
            )

    @classmethod
    def start_processing(cls, chat_message: ChatMessage) -> None:
        if chat_message.message is not None:
            cls.processing_messages[chat_message.message.group_id] = chat_message

    @classmethod
    def stop_processing(cls, chat_message: ChatMessage) -> None:
        if (
            chat_message.message is not None
            and cls.processing_messages.get(chat_message.message.group_id)
            is chat_message
        ):
            del cls.processing_messages[chat_message.message.group_id]
