import re

from ATRI.scheduler import SchedulerController, SchedulerJob

_QUALIFIER = re.compile(r"[:：]")


def iter_jobs(service: str | None = None) -> list[tuple[str, str, SchedulerJob]]:
    """按 ``(服务, 任务名, 任务对象)`` 列出任务；``service`` 为空时列出全部。"""
    if service is not None:
        jobs = SchedulerController.service_schedulers.get(service, {})
        return [(service, name, job) for name, job in jobs.items()]
    return [
        (svc, name, job)
        for svc, jobs in SchedulerController.service_schedulers.items()
        for name, job in jobs.items()
    ]


def _next_run(job: SchedulerJob) -> str:
    next_run = job.next_run_time
    return next_run.strftime("%Y-%m-%d %H:%M:%S") if next_run else "-"


def describe_jobs(service: str | None = None) -> str:
    """把任务列表格式化成可直接发送的文本。"""
    rows = iter_jobs(service)
    if not rows:
        if service:
            return f"服务`{service}`下没有定时任务。"
        return "当前没有任何定时任务。"
    lines = ["服务 | 任务 | 触发器 | 状态 | 下次执行"]
    for svc, name, job in rows:
        state = "已暂停" if job.is_paused else "运行中"
        lines.append(
            f"{svc} | {name} | {job.trigger_text} | {state} | {_next_run(job)}"
        )
    return "\n".join(lines)


def resolve_job(token: str) -> tuple[SchedulerController, str]:
    """把用户输入解析成 ``(控制器, 任务名)``。

    支持 ``服务:任务名``（中英文冒号都行）与不带服务的任务名；后者必须
    在全部服务里唯一，否则抛 :class:`LookupError` 并列出候选。
    """
    token = (token or "").strip()
    if not token:
        raise LookupError("请给出任务名，例如：`/任务暂停 管理:过期封禁清理`")

    parts = _QUALIFIER.split(token, maxsplit=1)
    if len(parts) == 2:
        service, name = parts[0].strip(), parts[1].strip()
        jobs = SchedulerController.service_schedulers.get(service)
        if not jobs or name not in jobs:
            raise LookupError(f"找不到任务`{service}:{name}`")
        return SchedulerController(service), name

    matches = [
        (svc, name)
        for svc, jobs in SchedulerController.service_schedulers.items()
        for name in jobs
        if name == token
    ]
    if not matches:
        raise LookupError(f"找不到任务`{token}`")
    if len(matches) > 1:
        candidates = "、".join(f"{svc}:{name}" for svc, name in matches)
        raise LookupError(f"任务名`{token}`不唯一，请指定服务：{candidates}")
    service, name = matches[0]
    return SchedulerController(service), name
