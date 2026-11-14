import re
import time

from ATRI.log import log

from ..agent.history import get_history_messages
from ..agent.role import ATRI
from ..agent.state import state_prompt
from ..agent.telemetry import telemetry
from ..llm import ModelType, llm_manager

#: 标签白名单。只接受这里面列出的词——模型偶尔会自创标签。
ALLOWED_TAGS = (
    "求助",
    "延续",
    "相关",
    "想说话",
    "相关事件",
    "不宜打扰",
    "不想说话",
)

#: 匹配 `标签:xxx`（中英文冒号都接受，前后允许空白）
_TAG_RE = re.compile(r"标签\s*[:：]\s*([^\s，,。；;]+)")

#: 行首的列表符号与强调标记。
_LINE_NOISE_RE = re.compile(r"^[\s*#>`\-\d.、)）]+")

#: 结论的正负说法。「不需要回复」含「需要回复」，靠否定词区分。
_VERDICT_RE = re.compile(r"(不需要回复|需要回复|不回复|回复)")
_POSITIVE_WORDS = frozenset({"需要回复", "回复"})
_NEGATED_RE = re.compile(r"(不|没|无|未|别|莫)[^，。；,;]{0,3}$")
#: 设问（「是否需要回复」）不是结论。
_QUESTION_RE = re.compile(r"(是否|要不要|该不该|需不需要)")


def _parse_verdict(content: str) -> bool | None:
    """抽出「回不回」；抽不出返回 None（约 8% 的输出是整段散文）。

    先认独占一行的结论，认不出只看最后一行：中间的「是否需要回复」是设问，
    拿它当结论等于替模型把话说完。
    """
    body = content or ""
    for raw in body.splitlines():
        line = _LINE_NOISE_RE.sub("", raw).strip()
        if line.startswith(("不需要回复", "不回复")):
            return False
        if line.startswith(("需要回复", "回复")):
            return True

    # 退回：只看最后一行，别把设问当结论。
    tail = next((line for line in reversed(body.splitlines()) if line.strip()), "")
    last: tuple[int, bool] | None = None
    for match in _VERDICT_RE.finditer(tail):
        word = match.group(1)
        lead = tail[max(0, match.start() - 10) : match.start()]
        if _QUESTION_RE.search(lead):
            continue
        positive = word in _POSITIVE_WORDS
        if positive and _NEGATED_RE.search(lead):
            positive = False
        last = (match.start(), positive)
    if last is None:
        return None
    return last[1]


def _parse_tag(content: str) -> str | None:
    """从判断输出里抽出分类标签；不在白名单内返回 None。

    None 与「抽到了但不在名单」都记成空，避免污染 groupby——
    宁可少一个样本，也不要一个错样本。
    """
    m = _TAG_RE.search(content or "")
    if not m:
        return None
    raw = m.group(1).strip()
    if raw in ALLOWED_TAGS:
        return raw
    for tag in ALLOWED_TAGS:
        if raw.startswith(tag):
            return tag
    return None


class JudgmentModel:
    """判断「这条消息要不要回应」。

    原版只看**对方**：有人求助、或对话需要延续才回；以及两条不打扰原则。
    那套判据是完备的，但它回答的是「**该不该**回」，没有「**想不想**回」。
    于是她的每一次开口都只有一个理由：别人需要她。

    P2 在保留原有两条不打扰原则的前提下，补上**关于她自己**的三条动机。
    注意措辞：这些是「可以回」的理由，不是「必须回」的命令——
    最终仍要她自己在思考层决定说什么、或者干脆只回很短一句。
    """

    prompt = """# 角色定位
你是一个专业的插话回复判断器，擅长根据多人对话历史判断当前聊天内容目标角色是否需要回复来进行插话。

# 目标角色
{role}

# 你此刻的状态
{state}

# 任务目标
当前对话没有提及到目标角色，需要你判断目标角色是否需要回复。
以下是**该回复**的场景。
1. 强烈的求助
2. 同一用户上个历史与目标角色对话过，且是对话的延续
3. 这个话题与她有关：涉及她记得的人或事、她正在做的事、她感兴趣的东西
4. 她此刻状态上想说话：心情好、或者正闷得慌
5. 刚发生了与她直接相关的事（被提到、被夸、被冒犯），她忍不住想说

以下场景**不要回复**（优先级高于上面，先满足这些再看上面）:
1. 当用户在历史中感到厌烦时
2. 当该用户正在与其他用户对话时
3. 她正在烦躁或很累，不想开口时
4. 这只是别人之间的寒暄，她插进去会很突兀

# 输出格式
请严格按以下两行输出，不要多写。

第一行只写「回复」或「不回复」。
第二行写原因，并在末尾附上**一个分类标签**。

分类标签只能取以下七个之一：
- 求助：有人明确提出需要帮助
- 延续：与她的既有对话在继续
- 相关：话题涉及她记得的人或事
- 想说话：她此刻状态上想开口
- 相关事件：刚发生了与她直接相关的事
- 不宜打扰：别人在聊私事/寒暄，她插进去突兀
- 不想说话：她烦躁或很累

例:
不回复
原因:当前用户正与另一个用户商量私事且不宜打搅。 标签:不宜打扰

例:
回复
原因:这正好是昨天她答应帮忙的那件事。 标签:延续

# 历史消息
{history_content}

# 开始分析
请根据聊天历史判断是否回复以下聊天内容：
{chat_content}"""

    @classmethod
    async def analyze(cls, bot, this_msg, history_list, chat_message) -> bool:
        if not llm_manager.has_type(ModelType.ATRI_JUDGE):
            return False
        chat_content = await this_msg.get_message(bot)
        messages = await get_history_messages(
            bot, history_list, chat_message.fallback_history
        )
        msg_his = "\n".join(messages) if messages else "无历史聊天记录"
        img_his = chat_message.image_history
        history = f"{img_his}\n\n{msg_his}"
        state_text = state_prompt() or "（状态平常）"
        started = time.perf_counter()
        replied = False
        reason: str | None = None
        tag: str | None = None
        parsed: bool | None = None
        try:
            response = await llm_manager.call_model_by_type(
                ModelType.ATRI_JUDGE,
                cls.prompt.format(
                    role=f"{ATRI.role_name}:{ATRI.personality}",
                    state=state_text,
                    history_content=history,
                    chat_content=chat_content,
                ),
                "Agent-主动回复判断",
            )
            content = response.content
            verdict = _parse_verdict(content)
            parsed = verdict is not None
            replied = bool(verdict)
            for line in content.splitlines():
                if "原因" in line:
                    reason = line.strip()[:120]
                    break
            tag = _parse_tag(content)
            return replied
        except Exception as e:
            log.error(f"回复判断失败：{e}")
            return False
        finally:
            telemetry.judge(
                chat_id=str(getattr(this_msg, "chat_id", "") or ""),
                replied=replied,
                duration=time.perf_counter() - started,
                reason=reason,
                tag=tag,
                parsed=parsed,
                text=chat_content,
            )
