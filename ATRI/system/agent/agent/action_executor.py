"""功能调用执行器（并行执行阶段）。

把「校验 → 分流 → 执行 → 回填」从思考循环里拆出来：

- 先完成全部校验，再派发；不会出现一半调用已经发出去、另一半才报参数错。
- 有返回值的调用按 ``parallel_safe`` 分 lane：可并发的 ``gather``，
  不可并发的串行，结果按模型输出顺序回填。
- 无返回值的副作用调用按模型输出顺序执行；``say`` / ``md`` / ``face`` /
  ``say_exact`` 投递到 :class:`ChatOutbox` 后立即返回，不阻塞思考。
- ``call`` 是控制类：先解析目标功能，再按目标的声明决定 lane。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from ATRI.exceptions import str_traceback
from ATRI.log import log

from .function_calling import (
    FunctionCalling,
    FunctionCallingData,
    FunctionCallingManager,
)
from .outbox import ChatOutbox, OutboxFullError, ReplyJob
from .telemetry import telemetry

#: 会进入聊天队列的功能。其余副作用直接在当前 lane 执行。
OUTBOX_FUNCTIONS = frozenset({"say", "md", "face"})

#: 逐字发送的唯一入口；跳过 Chat 渲染。
EXACT_FUNCTIONS = frozenset({"say_exact"})


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("true", "1", "yes", "是")


def _safe_count(value: Any) -> int:
    try:
        return max(1, min(3, int(value or 1)))
    except (TypeError, ValueError):
        return 1


def _available_faces() -> list[dict]:
    """给 Chat 模型选表情用的候选清单（id + 短描述）。"""
    try:
        from .memes import FaceManager

        options: list[dict] = []
        for meme in FaceManager.get_all_memes():
            desc = FaceManager.get_meme_description(meme)
            if not desc:
                continue
            short = str(desc).split("（")[0][:20]
            options.append({"id": meme, "desc": short})
        return options
    except Exception as exc:
        log.debug(f"读取表情候选失败，face 将不可用：{exc}")
        return []


#: 单个数据调用的超时时间（秒）。超时回填「调用 X 超时」。
DEFAULT_DATA_TIMEOUT = 60.0

#: 数据调用并发上限，避免模型一轮输出十个 recall 把 API 打满。
DEFAULT_MAX_PARALLEL = 4

#: 单轮副作用上限，防止模型刷屏。
DEFAULT_MAX_SIDE_EFFECTS = 5


@dataclass
class CallResult:
    name: str
    ok: bool
    value: Any = None
    error: str = ""
    duration: float = 0.0
    legacy_name: str = ""
    side_effect: bool = False


@dataclass
class ActionResult:
    feedback: list[str] = field(default_factory=list)
    keep_thinking: bool = False
    started_side_effects: int = 0
    results: list[CallResult] = field(default_factory=list)


@dataclass
class _Prepared:
    index: int
    raw_name: str
    name: str
    func: type[FunctionCalling]
    data: FunctionCallingData
    action: str
    returns_data: bool
    parallel_safe: bool
    target: str = ""


class ActionExecutor:
    def __init__(
        self,
        outbox: ChatOutbox | None = None,
        max_parallel: int = DEFAULT_MAX_PARALLEL,
        data_timeout: float = DEFAULT_DATA_TIMEOUT,
        max_side_effects: int = DEFAULT_MAX_SIDE_EFFECTS,
    ):
        self.outbox = outbox
        self.max_parallel = max(1, int(max_parallel))
        self.data_timeout = float(data_timeout)
        self.max_side_effects = max(1, int(max_side_effects))
        self._semaphore = asyncio.Semaphore(self.max_parallel)
        # 累计整回合的副作用次数：executor 在 thinking 里跨轮复用，
        # 不能每轮重新计数。主要约束 ``love`` / ``rel_set`` / ``remember``
        # 这类状态写入；``say`` 另有 outbox 队列上限。
        self._side_total = 0

    async def run(
        self,
        calls: list[dict],
        ctx: FunctionCallingData,
        issues: list[str] | None = None,
    ) -> ActionResult:
        feedback_by_index: dict[int, list[str]] = {}
        results: list[CallResult] = []
        keep_thinking = False

        if issues:
            detail = "；".join(issues)
            feedback_by_index[-1] = [
                f"本次输出有 {len(issues)} 处调用无法解析：{detail}。"
                "请严格按「功能名(参数名=参数值, ...)」格式重写：字符串用双引号，"
                "需要换行写 \\n，每个调用独占一行，不要用代码块包裹"
            ]
            keep_thinking = True

        prepared: list[_Prepared] = []
        for index, call in enumerate(calls):
            item, problems = self._validate(call, index, ctx)
            if problems:
                feedback_by_index[index] = problems
                keep_thinking = True
                continue
            assert item is not None
            prepared.append(item)

        side_calls = [item for item in prepared if not item.returns_data]
        data_calls = [item for item in prepared if item.returns_data]
        serial_data = [item for item in data_calls if not item.parallel_safe]
        parallel_data = [item for item in data_calls if item.parallel_safe]

        # ---- 副作用：按模型输出顺序执行 / 入队 ----
        started = 0
        for item in side_calls:
            if self._side_total >= self.max_side_effects:
                feedback_by_index.setdefault(item.index, []).append(
                    f"本轮调用次数过多，已忽略 {item.name}"
                )
                continue
            self._side_total += 1
            started += 1
            result = await self._run_side(item)
            results.append(result)
            text = self._describe(item, result)
            if text:
                feedback_by_index.setdefault(item.index, []).append(text)
            elif not result.ok and "发言过多" in result.error:
                feedback_by_index.setdefault(item.index, []).append(
                    f"本轮发言过多，已忽略 {item.name}"
                )
            if not result.ok:
                # 副作用失败不污染决策：只记日志与 telemetry，下一轮
                # outbox.poll_feedback() 会带一条「上一条没发出去」。
                log.debug(f"副作用 {item.name} 失败，已忽略：{result.error}")

        # ---- 数据调用：串行 lane 按顺序，可并发 lane gather ----
        for item in serial_data:
            result = await self._run_data(item)
            results.append(result)
            feedback_by_index.setdefault(item.index, []).append(
                self._describe(item, result)
            )
            if result.ok and result.value is None:
                continue
            keep_thinking = True

        if parallel_data:
            gathered = await asyncio.gather(
                *(self._run_data(item) for item in parallel_data),
                return_exceptions=False,
            )
            for item, result in zip(parallel_data, gathered, strict=True):
                results.append(result)
                feedback_by_index.setdefault(item.index, []).append(
                    self._describe(item, result)
                )
                if result.ok and result.value is None:
                    continue
                keep_thinking = True

        feedback: list[str] = []
        for index in sorted(feedback_by_index):
            feedback.extend(feedback_by_index[index])

        return ActionResult(
            feedback=feedback,
            keep_thinking=keep_thinking,
            started_side_effects=started,
            results=results,
        )

    # ---- 校验 ----

    def _validate(
        self,
        call: dict,
        index: int,
        ctx: FunctionCallingData,
    ) -> tuple[_Prepared | None, list[str]]:
        from ..brain.action_parser import normalize_args

        raw_name = str(call.get("function") or "").strip()
        name = FunctionCallingManager.resolve_name(raw_name)
        if name != raw_name:
            log.debug(f"功能调用名 {raw_name} 是旧名，已归一为 {name}")
        func = FunctionCallingManager.function_calling.get(name)
        if func is None:
            log.debug(f"未知的调用功能:{name}，已跳过")
            known = "、".join(FunctionCallingManager.function_calling)
            return None, [f"未知的调用功能:{name}，可用功能:{known}"]

        check = FunctionCallingManager.availability(
            name, ctx.sender, ctx.sender_id, ctx.permission
        )
        if not check.available:
            reason = check.reason or "该功能已被关闭"
            log.debug(f"不可用的调用功能:{name}（{reason}），已跳过")
            return None, [f"功能 {name} 当前不可用：{reason}"]

        # 不常用功能只能由 call 调用：直接写名字给了更明确的重写提示
        if FunctionCallingManager.is_rare(name):
            log.debug(f"不常用功能被直接调用:{name}，已引导改用 call")
            return None, [
                f"功能 {name} 是不常用功能，不能直接调用。"
                f'请改用 call(function="{name}", args={{...}})'
            ]

        args, arg_issues = normalize_args(func, call.get("args") or {})
        if arg_issues:
            log.debug(f"功能 {name} 参数不合规:{arg_issues}")
            detail = "；".join(arg_issues)
            return None, [
                f"功能 {name} 的参数不合规：{detail}。请对照上面的参数声明重写本次调用"
            ]

        data = FunctionCallingData(
            ctx.sender_id,
            ctx.chat_id,
            ctx.sender,
            args,
            ctx.current_history,
            bot=ctx.bot,
            permission=ctx.permission,
        )
        action = FunctionCallingManager.action_for(name, data)
        if action:
            # 第一次求值已经写过拒绝埋点，这里只补充动作级作用域的原因。
            action_check = FunctionCallingManager.availability(
                name,
                ctx.sender,
                ctx.sender_id,
                ctx.permission,
                action,
                record=False,
            )
            if not action_check.available:
                reason = action_check.reason or "该功能已被关闭"
                log.debug(f"不可用的调用功能:{name}（{reason}），已跳过")
                return None, [f"功能 {name} 当前不可用：{reason}"]

        returns_data = bool(getattr(func, "returns_data", True))
        # say_exact 也是副作用：逐字发送，不经过 Chat 渲染
        if name in EXACT_FUNCTIONS:
            returns_data = False
        parallel_safe = bool(getattr(func, "parallel_safe", True))
        target = ""
        if name == "call":
            # 控制类：按目标功能决定 lane，不能把 call 整体当副作用。
            # 只有目标确实是已注册的不常用功能时才采信它的声明；否则
            # call 自己会返回「不是不常用功能」之类的数据，必须回填。
            target = FunctionCallingManager.resolve_name(
                str(args.get("function") or "").strip()
            )
            target_func = FunctionCallingManager.function_calling.get(target)
            if target_func is not None and FunctionCallingManager.is_rare(target):
                returns_data = bool(getattr(target_func, "returns_data", True))
                parallel_safe = bool(getattr(target_func, "parallel_safe", True))
            else:
                returns_data = True
                parallel_safe = True

        return (
            _Prepared(
                index=index,
                raw_name=raw_name,
                name=name,
                func=func,
                data=data,
                action=action,
                returns_data=returns_data,
                parallel_safe=parallel_safe,
                target=target,
            ),
            [],
        )

    # ---- 执行 ----

    @staticmethod
    def _build_job(item: _Prepared) -> ReplyJob:
        """把已校验的调用转成 Outbox 的 ReplyJob（只做参数映射，不碰发送）。"""
        data = item.data
        args = getattr(data, "data", {}) or {}
        legacy = item.raw_name if item.raw_name != item.name else ""
        if item.name == "say_exact":
            return ReplyJob(
                kind="exact",
                name=item.name,
                legacy_name=legacy,
                data=data,
                exact_text=str(args.get("text") or "").strip(),
                at=_as_bool(args.get("at")),
                seq=item.index,
            )
        if item.name == "md":
            return ReplyJob(
                kind="markdown",
                name=item.name,
                legacy_name=legacy,
                intent=str(args.get("intent") or "").strip(),
                tone=str(args.get("tone") or "").strip(),
                payload={
                    "image": str(args.get("image") or "").strip(),
                    "title": str(args.get("title") or "").strip(),
                },
                data=data,
                seq=item.index,
            )
        if item.name == "face":
            return ReplyJob(
                kind="face",
                name=item.name,
                legacy_name=legacy,
                intent=str(args.get("intent") or "").strip(),
                payload={
                    "with_text": _as_bool(args.get("with_text")),
                    "faces": _available_faces(),
                },
                data=data,
                seq=item.index,
            )
        return ReplyJob(
            kind="text",
            name=item.name,
            legacy_name=legacy,
            intent=str(args.get("intent") or "").strip(),
            tone=str(args.get("tone") or "").strip(),
            count=_safe_count(args.get("count")),
            at=_as_bool(args.get("at")),
            data=data,
            seq=item.index,
        )

    async def _run_side(self, item: _Prepared) -> CallResult:
        """副作用：聊天类入队，其余直接执行。"""
        started = time.perf_counter()
        is_chat = item.name in OUTBOX_FUNCTIONS or item.name in EXACT_FUNCTIONS
        if self.outbox is not None and is_chat:
            try:
                await self.outbox.submit(self._build_job(item))
            except OutboxFullError as exc:
                return CallResult(
                    name=item.name,
                    ok=False,
                    error=str(exc),
                    duration=time.perf_counter() - started,
                    legacy_name=item.raw_name if item.raw_name != item.name else "",
                    side_effect=True,
                )
            return CallResult(
                name=item.name,
                ok=True,
                duration=time.perf_counter() - started,
                legacy_name=item.raw_name if item.raw_name != item.name else "",
                side_effect=True,
            )

        try:
            result = await asyncio.wait_for(
                FunctionCallingManager.call(item.name, item.data),
                timeout=self.data_timeout,
            )
            if is_chat and result is not None and "发送失败" in str(result):
                # 聊天类约定返回 None；带「发送失败」的字符串是内部已经失败。
                telemetry.tool(
                    name=item.name,
                    legacy_name=item.raw_name if item.raw_name != item.name else None,
                    ok=False,
                    duration=time.perf_counter() - started,
                    error=str(result),
                )
                return CallResult(
                    name=item.name,
                    ok=False,
                    value=result,
                    error=str(result),
                    duration=time.perf_counter() - started,
                    legacy_name=item.raw_name if item.raw_name != item.name else "",
                    side_effect=True,
                )
            telemetry.tool(
                name=item.name,
                legacy_name=item.raw_name if item.raw_name != item.name else None,
                ok=True,
                duration=time.perf_counter() - started,
            )
            return CallResult(
                name=item.name,
                ok=True,
                value=result,
                duration=time.perf_counter() - started,
                legacy_name=item.raw_name if item.raw_name != item.name else "",
                side_effect=True,
            )
        except TimeoutError:
            telemetry.tool(
                name=item.name,
                legacy_name=item.raw_name if item.raw_name != item.name else None,
                ok=False,
                duration=time.perf_counter() - started,
                error="timeout",
            )
            return CallResult(
                name=item.name,
                ok=False,
                error="timeout",
                duration=time.perf_counter() - started,
                legacy_name=item.raw_name if item.raw_name != item.name else "",
                side_effect=True,
            )
        except Exception as exc:
            telemetry.tool(
                name=item.name,
                legacy_name=item.raw_name if item.raw_name != item.name else None,
                ok=False,
                duration=time.perf_counter() - started,
                error=str(exc),
            )
            log.warning(f"功能调用失败 {item.name}:\n{str_traceback(exc)}")
            return CallResult(
                name=item.name,
                ok=False,
                error=str(exc),
                duration=time.perf_counter() - started,
                legacy_name=item.raw_name if item.raw_name != item.name else "",
                side_effect=True,
            )

    async def _run_data(self, item: _Prepared) -> CallResult:
        async with self._semaphore:
            started = time.perf_counter()
            try:
                value = await asyncio.wait_for(
                    FunctionCallingManager.call(item.name, item.data),
                    timeout=self.data_timeout,
                )
                telemetry.tool(
                    name=item.name,
                    legacy_name=item.raw_name if item.raw_name != item.name else None,
                    ok=True,
                    duration=time.perf_counter() - started,
                )
                return CallResult(
                    name=item.name,
                    ok=True,
                    value=value,
                    duration=time.perf_counter() - started,
                    legacy_name=item.raw_name if item.raw_name != item.name else "",
                )
            except TimeoutError:
                telemetry.tool(
                    name=item.name,
                    legacy_name=item.raw_name if item.raw_name != item.name else None,
                    ok=False,
                    duration=time.perf_counter() - started,
                    error="timeout",
                )
                return CallResult(
                    name=item.name,
                    ok=False,
                    error="timeout",
                    duration=time.perf_counter() - started,
                    legacy_name=item.raw_name if item.raw_name != item.name else "",
                )
            except Exception as exc:
                log.warning(f"功能调用失败 {item.name}:\n{str_traceback(exc)}")
                telemetry.tool(
                    name=item.name,
                    legacy_name=item.raw_name if item.raw_name != item.name else None,
                    ok=False,
                    duration=time.perf_counter() - started,
                    error=str(exc),
                )
                return CallResult(
                    name=item.name,
                    ok=False,
                    error=str(exc),
                    duration=time.perf_counter() - started,
                    legacy_name=item.raw_name if item.raw_name != item.name else "",
                )

    @staticmethod
    def _describe(item: _Prepared, result: CallResult) -> str:
        if not result.ok:
            if result.side_effect:
                # 副作用失败不回填（避免「发送失败」污染决策）
                return ""
            if result.error == "timeout":
                return f"调用{item.name}超时,请勿再次调用"
            return f"调用{item.name}失败,请勿再次调用"
        if result.side_effect or result.value is None:
            # 副作用调用的返回值一律丢弃：它只负责改状态，不需要模型消化。
            return f"调用{item.name}完成"
        return f"调用{item.name}成功,返回:{result.value}"
