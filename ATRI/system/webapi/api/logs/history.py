import re

from fastapi import APIRouter, Query
from nonebot.utils import run_sync

from ATRI.dir import DATA_DIR

from ...base_model import Result
from ...security import authentication

router = APIRouter(prefix="/logs")

LOGGER_DIR = DATA_DIR / "logs"
LOG_TYPES = ("info", "warning", "error", "debug")
"""日志文件按级别分目录存放，与 ATRI.log 的 sink 配置一致"""
_LOG_NAME_PATTERN = re.compile(r"^\d{8}-\d{2}\.log$")
"""文件名白名单（YYYYMMDD-HH.log），杜绝路径拼接注入"""


@router.get(
    "/files",
    dependencies=[authentication()],
    description="列出可检索的历史日志文件",
)
async def list_log_files() -> Result[list[dict]]:
    files: list[dict] = []
    for log_type in LOG_TYPES:
        folder = LOGGER_DIR / log_type
        if not folder.is_dir():
            continue
        for log_file in folder.glob("*.log"):
            if _LOG_NAME_PATTERN.match(log_file.name):
                files.append(
                    {
                        "type": log_type,
                        "name": log_file.name,
                        "size": log_file.stat().st_size,
                    }
                )
    files.sort(key=lambda item: (item["type"], item["name"]))
    return Result.ok(files)


def _read_and_filter(path, keyword: str) -> list[str]:
    """整读 + 过滤，放线程池执行：10MB 文件约百毫秒级，同步跑会阻塞事件循环"""
    # 单文件 rotation 上限 10MB，整读可接受；坏字节用替换符兜底
    with path.open(encoding="utf8", errors="replace") as f:
        lines = [line.rstrip("\r\n") for line in f]
    lines = [line for line in lines if line.strip()]
    if keyword:
        kw = keyword.lower()
        lines = [line for line in lines if kw in line.lower()]
    return lines


@router.get(
    "/history",
    dependencies=[authentication()],
    description="检索历史日志文件（关键字过滤 + 服务端分页）",
)
async def query_history(
    log_type: str = Query(default="info", alias="type"),
    name: str = Query(...),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=500, ge=1, le=2000),
    keyword: str = Query(default=""),
) -> Result[dict]:
    if log_type not in LOG_TYPES:
        return Result.fail("无效的日志类型")
    if not _LOG_NAME_PATTERN.match(name):
        return Result.fail("无效的日志文件名")
    path = LOGGER_DIR / log_type / name
    if not path.is_file():
        return Result.fail("日志文件不存在")

    lines = await run_sync(_read_and_filter)(path, keyword)

    total = len(lines)
    start = (page - 1) * size
    return Result.ok(
        {
            "total": total,
            "page": page,
            "size": size,
            "lines": lines[start : start + size],
        }
    )
