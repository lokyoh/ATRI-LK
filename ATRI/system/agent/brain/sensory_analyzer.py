import json
import re

from ..llm import ALL_MODEL_RESP_ERR, NO_MODEL_ERR, ModelType, llm_manager


class SensoryAnalyzer:
    prompt = """# 角色定位
你是一个专业的历史分析器，擅长从对话历史中提取情感信息。

# 任务目标
根据给定的聊天历史来分析当前对话人的信息，包括：
1. 情感倾向和情绪状态
2. 需求
3. 对话主题

# 输出格式
请按以下 JSON 格式输出分析结果：
{{
  "sensory": {{
    "total": "整体情感 积极/中性/消极",
    "tag": ["具体情绪标签"],
    "streng": 情绪强度 0-10 的数值
  }},
  "demand": ["需求列表"],
  "theme": "最新的对话主题"
}}

# 历史消息

{history_content}

# 开始分析
请根据聊天历史分析以下聊天内容：
{chat_content}"""

    @classmethod
    async def analyze(cls, history: str, chat_content: str) -> dict:
        try:
            response = await llm_manager.call_model_by_type(
                ModelType.TOOL,
                cls.prompt.format(history_content=history, chat_content=chat_content),
            )
            if response == NO_MODEL_ERR:
                raise RuntimeError(NO_MODEL_ERR)
            if response == ALL_MODEL_RESP_ERR:
                raise RuntimeError(ALL_MODEL_RESP_ERR)
            # 从响应中提取文本内容
            if isinstance(response, dict):
                content = response.get("content", "")
            else:
                content = str(response) if response else ""
            pattern = r"```json\s*(.*?)\s*```"
            match = re.search(pattern, content, re.DOTALL)
            if match:
                json_str = match.group(1).strip()
            else:
                json_str = content
            return json.loads(json_str)
        except Exception as e:
            raise ValueError(f"无法解析模型响应为 JSON 格式：{e}")
