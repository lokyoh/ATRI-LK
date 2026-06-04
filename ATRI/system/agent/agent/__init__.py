from asyncio import Lock

from nonebot.adapters.onebot.v11 import Message

from ATRI.log import log

from .history import chat_history, img_history, ChatHistory, ImageHistory, History
from .sender import ChatSender
from ..basic.chat import chat_model
from ..brain import ActionModel, JudgmentModel, ReplyModel, SensoryAnalyzer, ThinkingModel
from ..llm import llm_manager, ModelType


class ChatMessage:
    def __init__(self, message: History, user_id: str, s_c, s_j):
        self.message = message
        self.user_id = user_id
        self.force_chat = not s_c and s_j


class ATRIAgent:
    waiting_num = 0
    temp_messages: dict[str,list[ChatMessage]] = {}
    chat_lock = Lock()
    temp_lock = {}

    @classmethod
    async def chat(cls, bot, chat_sender: ChatSender, chat_id, user_id, message: Message, skip_chat=False,
                   skip_judgment=True):
        user_id = str(user_id)
        chat_id = str(chat_id)
        if chat_id not in cls.temp_lock:
            cls.temp_lock[chat_id] = Lock()
        async with cls.temp_lock[chat_id]:
            if chat_id not in cls.temp_messages:
                cls.temp_messages[chat_id] = []
            msg = await History.create(user_id, chat_id, message)
            cls.temp_messages[chat_id].append(ChatMessage(msg, user_id, skip_chat, skip_judgment))
        if skip_chat:
            return
        if cls.waiting_num and not skip_judgment:
            return
        cls.waiting_num += 1
        await cls.chat_lock.acquire()
        try:
            if cls.temp_messages[chat_id]:
                async with cls.temp_lock[chat_id]:
                    while True:
                        if not cls.temp_messages[chat_id]:
                            break
                        m = cls.temp_messages[chat_id].pop(0)
                        if chat_id not in chat_history:
                            chat_history[chat_id] = ChatHistory()
                        await chat_history[chat_id].add_history(m.user_id, chat_id, m.message)
                        if m.force_chat:
                            break
            await cls._chat(bot, chat_sender, chat_id, user_id, message, skip_chat, skip_judgment)
        except Exception:
            raise
        finally:
            cls.waiting_num -= 1
            cls.chat_lock.release()

    @classmethod
    async def _chat(cls, bot, chat_sender: ChatSender, chat_id, user_id, message: Message, skip_chat=False,
                   skip_judgment=True):
        chat_id = str(chat_id)
        user_id = str(user_id)
        if chat_id not in chat_history:
            chat_history[chat_id] = ChatHistory()
        if chat_id not in img_history:
            img_history[chat_id] = ImageHistory()
        c_h = chat_history[chat_id]
        i_h = img_history[chat_id]
        this_msg = c_h.get_last_history()
        if skip_chat:
            return
        if not llm_manager.has_type(ModelType.CHAT):
            log.warning("没有配置chat类型的模型")
            return
        if not llm_manager.has_type(ModelType.TOOL):
            log.warning("没有配置tool类型的模型")
            if skip_judgment:
                await chat_model.reply(bot, chat_id, user_id, chat_sender)
        msg = await this_msg.get_message(bot)
        msg_his = ""
        history_list = list(c_h.get_history()[:-1])
        if history_list:
            messages = [await h.get_message(bot) for h in history_list]
            msg_his += "\n".join(messages)
        else:
            msg_his += "无历史聊天记录"
        img_his = i_h.get_history()
        if not skip_judgment and message.extract_plain_text() == "":
            return
        sensory = await SensoryAnalyzer.analyze(f"{img_his}\n\n{msg_his}", msg)
        str_sensory = (f"整体情感:{sensory.get('sensory', {}).get('total', '未知')} "
                       f"强度:{sensory.get('sensory', {}).get('strength', '未知')} "
                       f"具体情感:{",".join(sensory.get('sensory', {}).get('tag', ['未知']))}\n"
                       f"当前对话主题:{sensory.get('theme', '未知')} "
                       f"对方需求:{','.join(sensory.get('demand', ['未知']))}")
        log.info(f"情感分析:{str_sensory}")
        if not skip_judgment:
            if not await JudgmentModel.analyze(f"{img_his}\n\n{msg_his}", msg, str_sensory):
                return
        thinking, functions_data = await ThinkingModel.thinking(bot, chat_id, user_id, str_sensory)
        thinking_list = [thinking]
        times = 0
        calling_backs = []
        while True:
            times += 1
            continue_chat, calling_back = await ActionModel.do_action(functions_data, user_id, chat_id)
            calling_backs += calling_back
            if continue_chat:
                thinking, functions_data = await ThinkingModel.continue_thinking(bot, chat_id, user_id, calling_back,
                                                                                 times >= 15)
                thinking_list.append(thinking)
            else:
                break
        await ReplyModel.reply(bot, chat_id, user_id, thinking_list, calling_backs, chat_sender, with_tts=skip_judgment)
