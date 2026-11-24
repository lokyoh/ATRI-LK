from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ATRI.log import log

from ..agent.reply_pipeline import (
    FACE_TEXT_CHARS,
    MARKDOWN_BODY_CHARS,
    parse_face,
    parse_markdown,
    parse_messages,
)
from ..llm import llm_manager

RenderKind = Literal["text", "markdown", "face"]

#: 历史只取最近 N 条
HISTORY_LIMIT = 10

#: 首次失败后再试一次
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
    user_id: str = ""


@dataclass
class ChatRenderRequest:
    kind: RenderKind
    intent: str
    tone: str = ""
    count: int = 1
    at: bool = False
    payload: dict = field(default_factory=dict)
    """text 无；markdown 支持 title；face 支持 faces / with_text"""

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
        if not llm_manager.has_slot("chat"):
            log.warning("聊天模型未配置，已降级为 intent 直发")
            return cls._degrade(request)
        prompt = cls._build_prompt(request, ctx)
        last_error = ""
        for attempt in range(RETRIES + 1):
            try:
                response = await llm_manager.call_agent(
                    "chat",
                    prompt,
                    f"Agent-Chat表达({request.kind})",
                    app_id="chat",
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
    # 稳定段在前、逐轮变的后置，前缀缓存才命中。
    # 三种 kind 共用一个静态头，状态与对话只在尾部才出现差异。

    @classmethod
    def _static_prompt(cls, ctx: ChatContext) -> str:
        return (
            f"你是{ctx.role_name}。你正在一个 QQ 群里聊天。\n"
            f"# 你的性格\n{ctx.personality or '自然、口语'}\n"
            "# 说话要求\n"
            "- 像在群里打字，不像写文章\n"
            "- 口语，可以用语气词；不用书面连接词\n"
            "- 不写动作描写、不写括号心理活动\n"
            "- 不复述对方刚说的话\n"
            "- 只表达「决策」里的意思，不新增事实、不解释、不总结\n"
            "- 一件事一句话说完。不要为同一件事换几种说法反复讲，也不要把它拆成几条\n"
            "- 只有决策里确实是两件不同的事，才分成两条说\n"
            "- 别人已经把话说完了就顺着接，不要每句都回一句评价\n"
        )

    @classmethod
    def _context_prompt(cls, ctx: ChatContext) -> str:
        history = "\n".join(ctx.history[-HISTORY_LIMIT:]) or "（无）"
        return (
            f"# 你此刻的状态\n{ctx.state or '平静'}\n"
            f"# 你和对方的关系\n{ctx.relation or '普通群友'}\n"
            f"# 最近的对话\n{history}\n"
        )

    @classmethod
    def _build_prompt(cls, request: ChatRenderRequest, ctx: ChatContext) -> str:
        tone = request.tone or "自然"
        static = cls._static_prompt(ctx)
        if request.kind == "markdown":
            title = str(request.payload.get("title") or "")
            return (
                static
                + "# 决策\n"
                + "你要发一张卡片，用来讲长内容或需要排版的东西。\n"
                + "# 输出\n"
                + '只输出 JSON：{"title": "标题", "body": "正文"}\n'
                + "正文允许 Markdown：小标题、列表、表格、代码块都能用。\n"
                + "代码块用 ``` 围起来；但不要插图、不要写图片链接。\n"
                + cls._context_prompt(ctx)
                + f"# 本次\n表达：{request.intent}\n"
                + f"语气：{tone}\n"
                + f"可用标题：{title or '由你决定'}"
            )
        if request.kind == "face":
            faces = request.payload.get("faces") or []
            lines = [
                f"- id={item.get('id')} | {item.get('desc', '')}" for item in faces
            ]
            return (
                static
                + "# 决策\n"
                + "你想发一个表情。\n"
                + "可选表情：\n"
                + ("\n".join(lines) or "（无可用表情）")
                + "\n# 输出\n"
                + '只输出 JSON：{"face": "id", "text": "可选的一句话"}\n'
                + '没有合适的就输出：{"face": ""}\n'
                + cls._context_prompt(ctx)
                + f"# 本次\n表达：{request.intent}。"
            )
        count = max(1, min(3, int(request.count or 1)))
        return (
            static
            + "# 决策\n"
            + "你要在群里说话。\n"
            + "# 输出\n"
            + '只输出 JSON：{"messages": ["第一条", "第二条"]}\n'
            + cls._context_prompt(ctx)
            + f"# 本次\n表达：{request.intent}\n"
            + f"语气：{tone}\n"
            + f"希望条数：{count}"
        )

    # ---- 解析 / 降级 ----

    @classmethod
    def _parse(cls, request: ChatRenderRequest, raw: str) -> RenderedReply:
        if request.kind == "markdown":
            parsed = parse_markdown(raw, request.intent)
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

            return RenderedReply(
                kind="markdown",
                markdown={
                    "title": str(request.payload.get("title") or "")[:40],
                    "body": sanitize(intent)[:MARKDOWN_BODY_CHARS],
                },
                degraded=True,
            )
        from ..agent.reply_pipeline import split

        # 不传 max_chars，跟随 `reply_max_chars` 配置。
        messages = split(intent)[: max(1, request.count or 1)]
        return RenderedReply(kind="text", messages=messages, degraded=True)


def build_context(
    *,
    role_name: str = "亚托莉",
    personality: str = "",
    state: str = "",
    relation: str = "",
    history: list[str] | None = None,
    chat_id: str = "",
    user_id: str = "",
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
        user_id=str(user_id),
    )
