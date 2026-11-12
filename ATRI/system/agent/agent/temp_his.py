import time
from asyncio import Lock
from typing import ClassVar

from nonebot.adapters import Bot

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
        # 本次处理开始时队列里已有的消息。之后的每一轮思考
        # 都从这里往后取，避免把已经写进提示词的历史再喂一遍。
        self.seen_mids: set[int] = {h.mid for h in history}
        self.user_id = args.user_id
        self.force_chat = args.is_force_chat()
        self.time_stamp = time.time()

    def check_time(self) -> bool:
        return time.time() - self.time_stamp <= 180

    async def get_timely_messages_prompt(self, bot: Bot) -> str:
        """从本会话的消息队列里取出「处理开始后新到」的消息。

        队列由 `add_message` 统一写入，是消息进入 Agent 的**唯一入口**；
        这里不再维护单独的待处理列表，避免两条链路各存一份、出现
        「已经回过了又被当成新消息」的重复注入。
        """
        if self.message is None:
            return ""
        chat_id = self.message.chat_id
        try:
            queue = ChatHistoryManager.get_history(chat_id).get_history()
        except Exception as e:
            history_logger.warning(f"get_timely_messages_prompt: 读取队列失败: {e!r}")
            return ""

        messages: list[str] = []
        for history in queue:
            if history.mid in self.seen_mids:
                continue
            self.seen_mids.add(history.mid)
            # sender 为 None 是她自己发出的消息（回复 / 主动开口），不是新输入
            if history.sender is None:
                continue
            if str(history.sender) == str(bot.self_id):
                continue
            if history.message is None:
                continue
            try:
                messages.append(await history.get_message(bot))
            except BaseException as e:
                history_logger.warning(
                    f"get_timely_messages_prompt: get_message failed: {e!r}"
                )

        # 队列淘汰掉的 mid 不必继续占着集合；剩下的都是当前队列里的。
        queue_mids = {h.mid for h in queue}
        self.seen_mids &= queue_mids

        if not messages:
            return ""
        content = "\n".join(messages)
        return (
            "#本次处理期间收到的新消息\n"
            "以下消息是在当前任务开始处理后到达的，按接收顺序排列\n"
            f"{content}"
        )


class TempHisManager:
    temp_lock: ClassVar[dict[str, Lock]] = {}

    @classmethod
    async def add_message(cls, message, args: ChatArgs) -> ChatMessage:
        if args.chat_id not in cls.temp_lock:
            cls.temp_lock[args.chat_id] = Lock()
        async with cls.temp_lock[args.chat_id]:
            history = await History.create(
                args.bot.self_id, args.user_id, args.chat_id, message
            )
            if history is None:
                return ChatMessage(None, [], "", [], args)
            chat_history = ChatHistoryManager.get_history(args.chat_id)
            if not chat_history.fallback_history_loaded:
                if not chat_history.get_history():
                    chat_history.fallback_history = history_logger.get_recent_messages(
                        args.chat_id, config.get("max_history")
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
