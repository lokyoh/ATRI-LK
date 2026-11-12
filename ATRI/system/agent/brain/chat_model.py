"""Chat 模型：把 thinking 的「意图」渲染成最终表达（计划 3.5.3）。

thinking 只决定「要表达什么」，这里决定「怎么说」。三种模式共用同一段
system（角色 / 状态 / 关系 / 最近对话 / 说话要求），但**不传工具列表、
不传 DSL、不传技能正文**——这是纯表达模型，避免它模仿 thinking 的调用格式。

失败时按 3.5.7 降级：text / markdown 用 intent 兜底，face 直接丢弃。
降级不调用旧 `CHAT` / `TOOL` 类型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ATRI.log import log

from ..agent.reply_pipeline import (
    FACE_TEXT_CHARS,
    REPLY_MAX_CHARS,
    parse_face,
    parse_markdown,
    parse_messages,
)
from ..llm import ModelType, llm_manager

RenderKind = Literal["text", "markdown", "face"]

#: 各模式的采样参数。thinking 保持 0.3，这里是「表达」不是「决策」。
TEMPERATURE = {"text": 0.8, "markdown": 0.5, "face": 0.8}

#: 各模式的输出上限
MAX_TOKENS = {"text": 300, "markdown": 600, "face": 80}

#: 历史只取最近 N 条
HISTORY_LIMIT = 10

#: 不同模式的重试次数（首次失败后再试一次）
RETRIES = 1


@dataclass
class ChatContext:
    """渲染用的不可变快照；并行渲染时每个 job 各带一份。"""

    role_name: str = "亚托莉"
    personality: str = ""
    state: str = ""
    relation: str = ""
    history: list[str] = field(default_factory=list)
    chat_id: str = ""


@dataclass
class ChatRenderRequest:
    kind: RenderKind
    intent: str
    tone: str = ""
    count: int = 1
    at: bool = False
    payload: dict = field(default_factory=dict)
    """text 无；markdown 支持 image / title；face 支持 faces / with_text"""

    seq: int = 0
    snapshot_id: str = ""


@dataclass
class RenderedReply:
    kind: str
    messages: list[str] = field(default_factory=list)
    markdown: dict = field(default_factory=dict)
    face: dict = field(default_factory=dict)
    degraded: bool = False


class ChatModel:
    @classmethod
    async def render(
        cls, request: ChatRenderRequest, ctx: ChatContext | None = None
    ) -> RenderedReply:
        ctx = ctx or ChatContext()
        if not llm_manager.has_type(ModelType.ATRI_CHAT):
            log.warning("ATRI_CHAT 未配置，已降级为 intent 直发")
            return cls._degrade(request)
        prompt = cls._build_prompt(request, ctx)
        last_error = ""
        for attempt in range(RETRIES + 1):
            try:
                response = await llm_manager.call_agent(
                    ModelType.ATRI_CHAT,
                    prompt,
                    f"Agent-Chat表达({request.kind})",
                )
            except Exception as exc:
                last_error = str(exc)
                log.warning(f"Chat 渲染失败（第 {attempt + 1} 次）：{exc}")
                continue
            rendered = cls._parse(request, str(getattr(response, "content", "") or ""))
            if cls._is_empty(rendered):
                last_error = "空输出"
                continue
            return rendered
        log.warning(f"Chat 渲染放弃，降级为 intent：{last_error}")
        return cls._degrade(request)

    # ---- prompt ----

    @classmethod
    def _system_prompt(cls, request: ChatRenderRequest, ctx: ChatContext) -> str:
        history = "\n".join(ctx.history[-HISTORY_LIMIT:]) or "（无）"
        return (
            f"你是{ctx.role_name}。你正在一个 QQ 群里聊天。\n"
            f"# 你的性格\n{ctx.personality or '自然、口语'}\n"
            f"# 你此刻的状态\n{ctx.state or '平静'}\n"
            f"# 你和对方的关系\n{ctx.relation or '普通群友'}\n"
            f"# 最近的对话\n{history}\n"
            "# 说话要求\n"
            "- 像在群里打字，不像写文章\n"
            "- 口语，可以用语气词；不用书面连接词\n"
            "- 不写动作描写、不写括号心理活动\n"
            "- 不复述对方刚说的话\n"
            "- 只表达「决策」里的意思，不新增事实、不解释、不总结\n"
        )

    @classmethod
    def _build_prompt(cls, request: ChatRenderRequest, ctx: ChatContext) -> str:
        head = cls._system_prompt(request, ctx)
        tone = request.tone or "自然"
        if request.kind == "markdown":
            image = str(request.payload.get("image") or "")
            title = str(request.payload.get("title") or "")
            return (
                head
                + "\n# 决策\n"
                + f"你要发一张卡片，表达：{request.intent}\n"
                + f"语气：{tone}\n"
                + f"可用标题：{title or '由你决定'}\n"
                + f"可用图片：{image or '无'}\n"
                + "# 输出\n"
                + '只输出 JSON：{"title": "标题", "body": "正文"}\n'
                + "正文可以是 Markdown，但不要输出代码围栏，不要新增图片链接。"
            )
        if request.kind == "face":
            faces = request.payload.get("faces") or []
            lines = [
                f"- id={item.get('id')} | {item.get('desc', '')}" for item in faces
            ]
            return (
                head
                + "\n# 决策\n"
                + f"你想发一个表情，表达：{request.intent}\n"
                + "可选表情：\n"
                + ("\n".join(lines) or "（无可用表情）")
                + "\n# 输出\n"
                + '只输出 JSON：{"face": "id", "text": "可选的一句话"}\n'
                + '没有合适的就输出：{"face": ""}'
            )
        count = max(1, min(3, int(request.count or 1)))
        return (
            head
            + "\n# 决策\n"
            + f"你决定说：{request.intent}\n"
            + f"语气：{tone}\n"
            + f"希望条数：{count}\n"
            + "# 输出\n"
            + '只输出 JSON：{"messages": ["第一条", "第二条"]}'
        )

    # ---- 解析 / 降级 ----

    @classmethod
    def _parse(cls, request: ChatRenderRequest, raw: str) -> RenderedReply:
        if request.kind == "markdown":
            image = str(request.payload.get("image") or "")
            parsed = parse_markdown(raw, request.intent, image)
            if not parsed["title"] and request.payload.get("title"):
                parsed["title"] = str(request.payload["title"])[:40]
            if not parsed["body"]:
                return RenderedReply(kind="markdown", degraded=True)
            return RenderedReply(kind="markdown", markdown=parsed)
        if request.kind == "face":
            faces = request.payload.get("faces") or []
            parsed = parse_face(raw, faces)
            if not request.payload.get("with_text"):
                parsed["text"] = ""
            if not parsed["id"]:
                return RenderedReply(kind="face", degraded=True)
            parsed["text"] = parsed["text"][:FACE_TEXT_CHARS]
            return RenderedReply(kind="face", face=parsed)
        messages = parse_messages(raw, request.intent, request.count)
        return RenderedReply(kind="text", messages=messages)

    @classmethod
    def _is_empty(cls, rendered: RenderedReply) -> bool:
        if rendered.kind == "text":
            return not rendered.messages
        if rendered.kind == "markdown":
            return not rendered.markdown.get("body")
        return not rendered.face.get("id")

    @classmethod
    def _degrade(cls, request: ChatRenderRequest) -> RenderedReply:
        intent = str(request.intent or "").strip()
        if request.kind == "face":
            # face 没有可降级的表达：直接丢弃，让 thinking 继续
            return RenderedReply(kind="face", degraded=True)
        if request.kind == "markdown":
            from ..agent.reply_pipeline import sanitize

            body = sanitize(intent)[:800]
            return RenderedReply(
                kind="markdown",
                markdown={
                    "title": str(request.payload.get("title") or "")[:40],
                    "body": body,
                    "image": str(request.payload.get("image") or ""),
                },
                degraded=True,
            )
        from ..agent.reply_pipeline import split

        messages = split(intent, REPLY_MAX_CHARS)[: max(1, request.count or 1)]
        return RenderedReply(kind="text", messages=messages, degraded=True)


def build_context(
    *,
    role_name: str = "亚托莉",
    personality: str = "",
    state: str = "",
    relation: str = "",
    history: list[str] | None = None,
    chat_id: str = "",
) -> ChatContext:
    """从 thinking 侧收集渲染上下文；history 只保留最近 N 条。"""
    items = [str(item).strip() for item in (history or []) if str(item).strip()]
    return ChatContext(
        role_name=role_name,
        personality=personality,
        state=state,
        relation=relation,
        history=items[-HISTORY_LIMIT:],
        chat_id=str(chat_id),
    )


def now_label() -> str:
    from ATRI.utils.datetime import now

    return now().strftime("%H:%M")


def face_options(
    memes: list[str], descriptions: dict[str, str]
) -> list[dict[str, object]]:
    """把表情名与描述整理成 Chat 模型的候选清单。"""
    options: list[dict[str, object]] = []
    for meme in memes:
        desc = str(descriptions.get(meme) or "").strip()
        if not desc:
            continue
        options.append({"id": meme, "desc": desc.split("（")[0][:20]})
    return options
