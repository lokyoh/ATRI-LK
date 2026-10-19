from ATRI.log import log

from ..agent.history import get_history_messages
from ..agent.role import ATRI
from ..llm import ModelType, llm_manager


class JudgmentModel:
    prompt = """# 角色定位
你是一个专业的插话回复判断器，擅长根据多人对话历史判断当前聊天内容目标角色是否需要回复来进行插话。

# 目标角色
{role}

# 任务目标
当前对话没有提及到目标角色，尽量不要回答，但也要根据给定的聊天历史与当前聊天内容来分析目标角色是否需要回复与原因。
以下是回复场景。
1. 强烈的求助
2. 同一用户上个历史与目标角色对话过，且是对话的延续
以下场景不要回复:
1. 当用户在历史中感到厌烦时
2. 当该用户正在与其他用户对话时

# 输出格式
请按以下格式输出分析结果
是否需要回复
原因:当前判断的原因。

例:
不回复
原因:当前用户正与另一个用户商量私事且不宜打搅。

# 历史消息
{history_content}

# 开始分析
请根据聊天历史判断是否回复以下聊天内容：
{chat_content}"""

    @classmethod
    async def analyze(cls, bot, this_msg, history_list, chat_message) -> bool:
        if not llm_manager.has_type(ModelType.TOOL):
            return False
        chat_content = await this_msg.get_message(bot)
        messages = await get_history_messages(
            bot, history_list, chat_message.fallback_history
        )
        msg_his = "\n".join(messages) if messages else "无历史聊天记录"
        img_his = chat_message.image_history
        history = f"{img_his}\n\n{msg_his}"
        if not llm_manager.has_type(ModelType.TOOL):
            return False
        try:
            response = await llm_manager.call_model_by_type(
                ModelType.TOOL,
                cls.prompt.format(
                    role=f"{ATRI.role_name}:{ATRI.personality}",
                    history_content=history,
                    chat_content=chat_content,
                ),
                "Agent-主动回复判断",
            )
            content = response.content
            return bool(content.startswith(("回复", "需要回复")))
        except Exception as e:
            log.error(f"回复判断失败：{e}")
            return False
