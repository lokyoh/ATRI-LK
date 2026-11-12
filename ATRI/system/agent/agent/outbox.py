"""聊天输出队列（计划 3.5.5）。

thinking 只产出意图；worker 逐条完成「渲染 → 清洗 → 分条 → 发送 → 写历史」。
单 worker 保证顺序，渲染与发送不占用思考轮次；回合结束 `drain()` 后
`sender.sent_count`、历史与主动发言限流记账都已落定。

与 P3 的最小版区别：``say`` / ``md`` / ``face`` 不再直接发送，而是构造
``ReplyJob`` 交给这里；``say_exact`` 是唯一跳过 Chat 渲染的入口。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from ATRI.exceptions import str_traceback
from ATRI.log import log

from .reply_pipeline import dedupe
from .telemetry import telemetry

#: 单轮最多排队的聊天输出条数。超过后拒绝新的 say / md / face。
DEFAULT_MAX_QUEUE = 10

#: 条间自然停顿（秒），避免像机器人连发。
DEFAULT_INTER_MESSAGE_DELAY = 0.4


class OutboxFullError(RuntimeError):
    """队列已满，本轮发言过多。"""


@dataclass
class ReplyJob:
    """一条待渲染/发送的聊天输出。"""

    kind: str
    """text / markdown / face / exact"""

    intent: str = ""
    tone: str = ""
    count: int = 1
    at: bool = False
    payload: dict = field(default_factory=dict)
    exact_text: str = ""
    name: str = "say"
    """规范功能名，供 telemetry 使用"""

    legacy_name: str = ""
    data: Any = None
    """``FunctionCallingData``：sender / chat_id / history / bot"""

    seq: int = 0
    """模型输出顺序"""


@dataclass
class ChatOutbox:
    """每个回合一个队列 + 一个 worker，保证输出顺序。"""

    ctx: Any = None
    """``ChatContext``；由 thinking 在创建 Outbox 时传入"""
    recent: list[str] | None = None
    """最近已发送文本，供跨条去重"""
    max_queue: int = DEFAULT_MAX_QUEUE
    job_timeout: float = 60.0
    inter_message_delay: float = DEFAULT_INTER_MESSAGE_DELAY
    parallel_render: bool = False
    """并行渲染：一次取多条排队任务同时交给 Chat 模型，发送仍严格保序"""
    max_parallel_render: int = 2
    """并行渲染同时进行的条数（1-3）"""
    _queue: asyncio.Queue[ReplyJob] = field(default_factory=asyncio.Queue, init=False)
    _worker: asyncio.Task | None = field(default=None, init=False)
    _idle: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _feedback: list[str] = field(default_factory=list, init=False)
    _sent: list[str] = field(default_factory=list, init=False)
    _submitted: int = field(default=0, init=False)
    _done: int = field(default=0, init=False)
    _closed: bool = field(default=False, init=False)
    _current_sender: Any = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._idle.set()

    # ---- 只读状态 ----

    @property
    def submitted_count(self) -> int:
        """本回合投递过的输出条数（含尚未执行的）。"""
        return self._submitted

    @property
    def done_count(self) -> int:
        return self._done

    @property
    def pending(self) -> int:
        return self._submitted - self._done

    @property
    def sent(self) -> list[str]:
        """本回合已经真正发出去的文本。"""
        return list(self._sent)

    # ---- 投递 ----

    async def submit(self, job: ReplyJob) -> None:
        """入队并立即返回；worker 在后台按 FIFO 渲染并发送。"""
        if self._closed:
            raise OutboxFullError("本轮聊天已结束，不能再投递输出")
        if self._queue.qsize() >= self.max_queue:
            telemetry.outbox(
                action="full",
                chat_id=str(getattr(job.data, "chat_id", "") or ""),
                pending=self.pending,
                sent=len(self._sent),
            )
            raise OutboxFullError("本轮发言过多，已忽略")
        self._submitted += 1
        self._idle.clear()
        await self._queue.put(job)
        self._ensure_worker()

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="atri-chat-outbox")

    async def _run(self) -> None:
        try:
            while True:
                jobs = [await self._queue.get()]
                if self.parallel_render and self.max_parallel_render > 1:
                    # 一次多取几条：渲染可以并发，发送仍在上面的 for 里按序执行。
                    while len(jobs) < self.max_parallel_render:
                        try:
                            jobs.append(self._queue.get_nowait())
                        except asyncio.QueueEmpty:
                            break
                try:
                    await self._execute_batch(jobs)
                finally:
                    for _ in jobs:
                        self._queue.task_done()
                    if self._queue.empty():
                        self._idle.set()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - 兜底，worker 不应静默死掉
            log.error(f"聊天输出队列异常退出:{str_traceback(exc)}")
            self._feedback.append("上一条消息没有发出去")
        finally:
            self._idle.set()

    async def _execute_batch(self, jobs: list[ReplyJob]) -> None:
        """按批执行：``parallel_render`` 时先并发渲染，再逐条按序发送。"""
        if len(jobs) == 1 or not self.parallel_render:
            for job in jobs:
                await self._execute(job)
            return
        targets = [job for job in jobs if job.kind != "exact"]
        rendered: dict[int, Any] = {}
        if targets:
            limit = max(1, min(3, int(self.max_parallel_render)))
            semaphore = asyncio.Semaphore(limit)

            async def render_one(job: ReplyJob):
                async with semaphore:
                    try:
                        return await asyncio.wait_for(
                            self._render(job), timeout=self.job_timeout
                        )
                    except Exception as exc:
                        # 渲染失败不在这里吞掉语义：交给 ``_execute`` 走正常的
                        # 超时/失败分支（它会再尝试一次并最终降级为 intent）。
                        log.debug(f"并行渲染 {job.name} 未完成：{exc}")
                        return None

            results = await asyncio.gather(*(render_one(job) for job in targets))
            rendered = {
                id(job): result
                for job, result in zip(targets, results, strict=True)
                if result is not None
            }
        for job in jobs:
            await self._execute(job, rendered.get(id(job)))

    # ---- 执行 ----

    async def _execute(self, job: ReplyJob, rendered: Any = None) -> None:
        started = time.perf_counter()
        completed = False
        self._current_sender = job.data.sender if job.data is not None else None
        try:
            if job.kind == "exact":
                await self._send_exact(job)
            else:
                if rendered is None:
                    rendered = await asyncio.wait_for(
                        self._render(job), timeout=self.job_timeout
                    )
                await self._send_rendered(job, rendered)
            telemetry.tool(
                name=job.name,
                legacy_name=job.legacy_name or None,
                ok=True,
                duration=time.perf_counter() - started,
            )
            completed = True
        except TimeoutError:
            log.warning(f"聊天输出 {job.name} 超时（{self.job_timeout}s）")
            self._feedback.append("上一条消息没有发出去")
            telemetry.outbox(
                action="timeout",
                chat_id=str(getattr(job.data, "chat_id", "") or ""),
                pending=self.pending,
                sent=len(self._sent),
            )
            telemetry.tool(
                name=job.name,
                legacy_name=job.legacy_name or None,
                ok=False,
                duration=time.perf_counter() - started,
                error="timeout",
            )
            completed = True
        except Exception as exc:
            log.warning(f"聊天输出 {job.name} 失败:{str_traceback(exc)}")
            self._feedback.append("上一条消息没有发出去")
            telemetry.outbox(
                action="failed",
                chat_id=str(getattr(job.data, "chat_id", "") or ""),
                pending=self.pending,
                sent=len(self._sent),
                reason=str(exc)[:200],
            )
            telemetry.tool(
                name=job.name,
                legacy_name=job.legacy_name or None,
                ok=False,
                duration=time.perf_counter() - started,
                error=str(exc),
            )
            completed = True
        finally:
            if completed:
                self._done += 1

    async def _render(self, job: ReplyJob):
        started = time.perf_counter()
        from ..brain.chat_model import (
            ChatModel,
            ChatRenderRequest,
            build_context,
        )

        request = ChatRenderRequest(
            kind=job.kind,
            intent=job.intent,
            tone=job.tone,
            count=job.count,
            at=job.at,
            payload=dict(job.payload),
            seq=job.seq,
        )
        ctx = self.ctx or build_context(
            chat_id=str(getattr(job.data, "chat_id", "") or "")
        )
        rendered = await ChatModel.render(request, ctx)
        telemetry.chat_render(
            kind=job.kind,
            degraded=rendered.degraded,
            seq=job.seq,
            chat_id=str(getattr(job.data, "chat_id", "") or ""),
            chars=len("".join(rendered.messages)) if rendered.messages else 0,
            duration=time.perf_counter() - started,
        )
        return rendered

    # ---- 发送 ----

    async def _send_rendered(self, job: ReplyJob, rendered) -> None:
        if rendered.kind == "text":
            messages = dedupe(list(rendered.messages), job.data.chat_id)
            if not messages and job.intent:
                from .reply_pipeline import sanitize, split

                messages = dedupe(split(sanitize(job.intent)), job.data.chat_id)
            await self._send_text(job, messages)
            return
        if rendered.kind == "markdown":
            await self._send_markdown(job, rendered.markdown)
            return
        if rendered.kind == "face":
            await self._send_face(job, rendered.face)

    async def _send_text(self, job: ReplyJob, messages: list[str]) -> None:
        from .function_calling import _with_mention
        from .sender import OneBotChatSender

        data = job.data
        sender = data.sender
        history = data.current_history
        for index, message in enumerate(messages):
            text = message
            if index == 0 and job.at:
                payload = _with_mention(text, data.mention_target, data.mtype)
            else:
                payload = text
            await sender.send(payload)
            self._sent.append(text)
            self._feedback.append(f"你刚才对群里说：「{text}」")
            if history is not None:
                history.add_response(text)
            # 只对唯一的最终文本做 TTS；分多条时逐条语音会把消息条数翻倍
            if len(messages) == 1 and isinstance(sender, OneBotChatSender):
                await self._maybe_tts(text)
            if index < len(messages) - 1 and self.inter_message_delay > 0:
                await asyncio.sleep(self.inter_message_delay)

    async def _maybe_tts(self, text: str) -> None:
        """TTS 失败不影响文字；表情不触发 TTS。"""
        try:
            from ATRI.message import rec_msg_from_path

            from ..llm import ModelType, llm_manager

            if not llm_manager.has_type(ModelType.ATRI_TTS):
                return
            file = await llm_manager.call_model_by_type(
                ModelType.ATRI_TTS, text, "Agent-语音"
            )
            await self._sender().send(rec_msg_from_path(file.content))
        except Exception as exc:
            log.debug(f"TTS 发送失败（忽略）：{exc}")

    def _sender(self):
        return self._current_sender

    async def _send_markdown(self, job: ReplyJob, payload: dict) -> None:
        from .function_calling_utils import send_markdown_document

        data = job.data
        history = data.current_history
        body = str(payload.get("body") or "").strip()
        if not body:
            return
        title = str(payload.get("title") or "").strip()
        md_file = await send_markdown_document(data, payload)
        self._sent.append(f"{title}\n{body}".strip())
        if history is not None:
            history.add_response(f"[md:{md_file}]")
        self._feedback.append(f"你刚才发了一张卡片：「{title or body[:20]}」")
        await self._maybe_tts(body)

    async def _send_face(self, job: ReplyJob, payload: dict) -> None:
        from .memes import FaceManager

        data = job.data
        sender = data.sender
        history = data.current_history
        face_id = str(payload.get("id") or "").strip()
        if not face_id:
            return
        face = FaceManager.get_face(face_id)
        if not face:
            log.debug(f"表情 {face_id} 不可用，已丢弃")
            return
        await sender.send(face)
        self._sent.append(f"[face:{face_id}]")
        if history is not None:
            history.add_response(f"[表情:{face_id}]")
        self._feedback.append(f"你刚才发了一个表情（{face_id}）")
        text = str(payload.get("text") or "").strip()
        if text:
            await sender.send(text)
            self._sent.append(text)
            self._feedback.append(f"并且说：「{text}」")

    async def _send_exact(self, job: ReplyJob) -> None:
        data = job.data
        sender = data.sender
        history = data.current_history
        text = str(job.exact_text or "").strip()
        if not text:
            return
        payload = text
        if job.at:
            from .function_calling import _with_mention

            payload = _with_mention(text, data.mention_target, data.mtype)
        await sender.send(payload)
        self._sent.append(text)
        self._feedback.append(f"你刚才对群里说：「{text}」")
        if history is not None:
            history.add_response(text)
        await self._maybe_tts(text)

    # ---- 排空 / 收尾 ----

    async def drain(self, timeout: float = 30.0) -> None:
        """等待队列排空；超时则取消剩余任务。"""
        if self._worker is None:
            return
        try:
            await asyncio.wait_for(self._idle.wait(), timeout)
        except TimeoutError:
            log.warning(f"聊天输出队列排空超时（{timeout}s），取消剩余任务")
            self._feedback.append("上一条消息没有发出去")
        finally:
            await self.close()
            telemetry.outbox(
                action="drained",
                pending=self.pending,
                sent=len(self._sent),
            )

    def poll_feedback(self) -> list[str]:
        """取走「已经说了什么 / 发送失败」，交给下一轮思考。"""
        notes = list(self._feedback)
        self._feedback.clear()
        return notes

    async def close(self) -> None:
        """关闭队列并取消未完成的 worker。可重复调用。"""
        self._closed = True
        worker = self._worker
        self._worker = None
        if worker is None or worker.done():
            return
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # pragma: no cover - 清理阶段不再抛
            log.debug(f"关闭聊天输出队列时忽略异常：{exc}")
