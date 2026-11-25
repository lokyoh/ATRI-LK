import json
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from nonebot.utils import run_sync
from pydantic import BaseModel

from ATRI.exceptions import str_traceback
from ATRI.log import log

from ....base_model import Result
from ....security import authentication

router = APIRouter()

MAX_FILES = 14
"""最多回溯多少个数据文件；与 telemetry.RETENTION_DAYS 对齐"""

MAX_ROWS = 20000
"""单次统计最多读多少行，避免一天几十万条时把接口拖死"""


class EventCount(BaseModel):
    event: str
    count: int


class TelemetryValue(BaseModel):
    key: str
    count: int


class TelemetrySummary(BaseModel):
    files: list[str]
    """参与统计的数据文件（文件名即日期）"""
    rows: int
    """读到的总行数（受 MAX_ROWS 限制）"""
    truncated: bool
    events: list[EventCount]
    tool: list[TelemetryValue]
    """按功能名统计的调用次数"""
    judge_tags: list[TelemetryValue]
    """插话判断的标签分布"""
    initiative_start: list[TelemetryValue]
    """主动开口被拒的原因分布"""
    turn: dict
    """对话轮次的汇总（总轮数、失败率、平均耗时）"""


def _telemetry_dir() -> Path | None:
    try:
        from ATRI.system.agent.service import plugin

        path = plugin.get_path() / "telemetry"
    except Exception:
        return None
    return path if path.is_dir() else None


def _pick_files(root: Path) -> list[Path]:
    files = sorted(root.glob("*.jsonl"), key=lambda p: p.name)
    return files[-MAX_FILES:]


def _bump(bucket: dict[str, int], key: str) -> None:
    if not key:
        return
    bucket[key] = bucket.get(key, 0) + 1


def _read_lines(path: Path) -> list[dict]:
    lines: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                lines.append(json.loads(line))
            except ValueError:
                continue
    return lines


def _collect(files: list[Path]) -> tuple[int, bool, list[dict]]:
    rows: list[dict] = []
    truncated = False
    for path in files:
        try:
            rows.extend(_read_lines(path))
        except OSError as exc:
            log.warning(f"读取埋点文件 {path} 失败：{exc}")
            continue
        if len(rows) >= MAX_ROWS:
            truncated = True
            rows = rows[:MAX_ROWS]
            break
    return len(rows), truncated, rows


@router.get(
    "/get_telemetry",
    dependencies=[authentication()],
    response_model=Result[TelemetrySummary],
    response_class=JSONResponse,
    description="汇总最近的 Agent 行为埋点",
)
async def _() -> Result[TelemetrySummary]:
    try:
        root = _telemetry_dir()
        if root is None:
            return Result.ok(
                TelemetrySummary(
                    files=[],
                    rows=0,
                    truncated=False,
                    events=[],
                    tool=[],
                    judge_tags=[],
                    initiative_start=[],
                    turn={},
                ),
                "还没有埋点数据哦",
            )
        files = _pick_files(root)
        # 最多 14 个文件、2 万行 JSON 解析，放线程池执行避免阻塞事件循环
        rows_count, truncated, rows = await run_sync(_collect)(files)

        events: dict[str, int] = {}
        tools: dict[str, int] = {}
        tags: dict[str, int] = {}
        starts: dict[str, int] = {}
        turn_total = 0
        turn_failed = 0
        turn_rounds = 0
        turn_duration = 0.0

        for row in rows:
            event = str(row.get("event") or "")
            _bump(events, event)
            if event == "tool":
                _bump(tools, str(row.get("name") or ""))
            elif event == "judge":
                # tag 为空不代表「没有判断」，只是这次没打标签，不计入分布。
                _bump(tags, str(row.get("tag") or ""))
            elif event == "initiative" and row.get("kind") == "start":
                # 只有主动开口的拒绝理由值得看；插话判断成功/失败不按原因归堆。
                if not row.get("allowed"):
                    _bump(starts, str(row.get("reason") or "(未给原因)"))
            elif event == "turn":
                turn_total += 1
                if row.get("failed"):
                    turn_failed += 1
                turn_rounds += int(row.get("rounds") or 0)
                turn_duration += float(row.get("duration") or 0.0)

        return Result.ok(
            TelemetrySummary(
                files=[path.name for path in files],
                rows=rows_count,
                truncated=truncated,
                events=[
                    EventCount(event=key, count=value)
                    for key, value in sorted(events.items(), key=lambda kv: -kv[1])
                ],
                tool=[
                    TelemetryValue(key=key, count=value)
                    for key, value in sorted(tools.items(), key=lambda kv: -kv[1])
                ],
                judge_tags=[
                    TelemetryValue(key=key, count=value)
                    for key, value in sorted(tags.items(), key=lambda kv: -kv[1])
                ],
                initiative_start=[
                    TelemetryValue(key=key, count=value)
                    for key, value in sorted(starts.items(), key=lambda kv: -kv[1])
                ],
                turn={
                    "turns": turn_total,
                    "failed": turn_failed,
                    "avg_rounds": round(turn_rounds / turn_total, 1)
                    if turn_total
                    else 0.0,
                    "avg_duration": (
                        round(turn_duration / turn_total, 2) if turn_total else 0.0
                    ),
                },
            ),
            "拿到信息啦!",
        )
    except Exception as e:
        log.error(f"{router.prefix}/get_telemetry 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.get(
    "/get_telemetry_raw",
    dependencies=[authentication()],
    response_model=Result[list[dict]],
    response_class=JSONResponse,
    description="按日期回放最近的原始埋点（最多 200 行）",
)
async def _(date: str = "") -> Result[list[dict]]:
    try:
        root = _telemetry_dir()
        if root is None:
            return Result.ok([], "还没有埋点数据哦")
        if date:
            # 只接受纯日期名，杜绝路径穿越。
            if not date.replace("-", "").isdigit() or len(date) != 10:
                return Result.fail("date 需要是 YYYY-MM-DD", 400)
            candidates = [root / f"{date}.jsonl", *sorted(root.glob(f"{date}.*.jsonl"))]
        else:
            candidates = _pick_files(root)[-1:]
        lines: list[dict] = []
        for path in candidates:
            if not path.is_file():
                continue
            try:
                # 原始回放按天整读（可能很大），同样放线程池
                lines.extend(await run_sync(_read_lines)(path))
            except OSError as exc:
                log.warning(f"读取埋点文件 {path} 失败：{exc}")
        return Result.ok(lines[-200:], "拿到信息啦!")
    except Exception as e:
        log.error(f"{router.prefix}/get_telemetry_raw 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


__all__ = ["router"]
