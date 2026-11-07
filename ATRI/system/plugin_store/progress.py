import time
from dataclasses import asdict, dataclass, field


@dataclass
class TaskProgress:
    name: str
    """插件名"""
    action: str
    """动作：install / update / remove / refresh"""
    stage: str
    """当前阶段，见 STAGE_LABELS"""
    detail: str = ""
    """补充说明，如依赖数量、校验问题"""
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    done: bool = False
    ok: bool = True
    message: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["elapsed"] = round(self.updated_at - self.started_at, 1)
        data["stage_label"] = STAGE_LABELS.get(self.stage, self.stage)
        return data


STAGE_LABELS = {
    "queued": "排队中",
    "fetch": "下载中",
    "resource": "下载资源",
    "validate": "校验中",
    "deps": "安装依赖",
    "load": "加载中",
    "reload": "热重载",
    "cleanup": "清理中",
    "done": "已完成",
    "failed": "失败",
}

_ACTIVE: dict[str, TaskProgress] = {}
"""正在进行的任务，键为插件名；完成后保留最近一条供前端展示结果。"""

_FINISHED_KEEP = 20
"""已完成任务保留条数，避免无限增长。"""


def start(
    plugin_name: str, action: str, stage: str = "queued", detail: str = ""
) -> TaskProgress:
    progress = TaskProgress(name=plugin_name, action=action, stage=stage, detail=detail)
    _ACTIVE[plugin_name] = progress
    return progress


def update(
    plugin_name: str, stage: str | None = None, detail: str | None = None
) -> None:
    progress = _ACTIVE.get(plugin_name)
    if progress is None or progress.done:
        return
    if stage is not None:
        progress.stage = stage
    if detail is not None:
        progress.detail = detail
    progress.updated_at = time.time()


def finish(plugin_name: str, ok: bool = True, message: str = "") -> None:
    progress = _ACTIVE.get(plugin_name)
    if progress is None:
        return
    progress.stage = "done" if ok else "failed"
    progress.done = True
    progress.ok = ok
    progress.message = message
    progress.updated_at = time.time()
    _trim()


def _trim() -> None:
    finished = [p for p in _ACTIVE.values() if p.done]
    if len(finished) <= _FINISHED_KEEP:
        return
    finished.sort(key=lambda p: p.updated_at)
    for stale in finished[: len(finished) - _FINISHED_KEEP]:
        _ACTIVE.pop(stale.name, None)


def snapshot() -> list[dict]:
    """当前任务列表：进行中的在前，其次按更新时间倒序。"""
    items = sorted(_ACTIVE.values(), key=lambda p: (p.done, -p.updated_at))
    return [p.to_dict() for p in items]


def is_busy() -> bool:
    return any(not p.done for p in _ACTIVE.values())


def clear() -> None:
    """供测试重置。"""
    _ACTIVE.clear()
