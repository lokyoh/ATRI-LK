"""全局调度管理 API：列出各服务的定时任务并暂停 / 恢复 / 立即执行。"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ATRI.exceptions import BotRuntimeError, str_traceback
from ATRI.log import log
from ATRI.scheduler import SchedulerController

from ....base_model import Result
from ....security import authentication

router = APIRouter(prefix="/scheduler")


class SchedulerJobInfo(BaseModel):
    service: str
    """所属服务"""
    name: str
    """任务名（服务内唯一）"""
    trigger: str
    """触发器的可读描述"""
    paused: bool
    """是否已暂停"""
    next_run: str | None = None
    """下次执行时间；暂停时为 null"""
    job_id: str = ""
    """调度器内部的作业 id（服务-任务名）"""


class SchedulerJobKey(BaseModel):
    service: str
    name: str


def _to_info(service: str, name: str, job) -> SchedulerJobInfo:
    next_run = job.next_run_time
    return SchedulerJobInfo(
        service=service,
        name=name,
        trigger=job.trigger_text,
        paused=job.is_paused,
        next_run=next_run.strftime("%Y-%m-%d %H:%M:%S") if next_run else None,
        job_id=str(getattr(job, "id", "") or ""),
    )


def _controller(payload: SchedulerJobKey) -> tuple[SchedulerController, str]:
    """只允许操作真实存在的任务；找不到时抛 LookupError。"""
    jobs = SchedulerController.service_schedulers.get(payload.service)
    if not jobs or payload.name not in jobs:
        raise LookupError(f"找不到任务 `{payload.service}:{payload.name}`")
    return SchedulerController(payload.service), payload.name


@router.get(
    "/get_scheduler_jobs",
    dependencies=[authentication()],
    response_model=Result[list[SchedulerJobInfo]],
    response_class=JSONResponse,
    description="列出全部服务的定时任务",
)
async def _(service: str = "") -> Result[list[SchedulerJobInfo]]:
    try:
        result: list[SchedulerJobInfo] = []
        for svc, jobs in SchedulerController.service_schedulers.items():
            if service and svc != service:
                continue
            for name, job in jobs.items():
                result.append(_to_info(svc, name, job))
        result.sort(key=lambda item: (item.service, item.name))
        return Result.ok(result, "拿到信息啦!")
    except Exception as e:
        log.error(f"{router.prefix}/get_scheduler_jobs 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/pause_scheduler_job",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="暂停一个定时任务",
)
async def _(payload: SchedulerJobKey) -> Result:
    try:
        controller, name = _controller(payload)
        controller.pause_job(name)
        return Result.ok(info=f"已暂停 `{payload.service}:{name}`")
    except LookupError as e:
        return Result.fail(str(e), 404)
    except BotRuntimeError as e:
        return Result.fail(str(e), 400)
    except Exception as e:
        log.error(f"{router.prefix}/pause_scheduler_job 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/resume_scheduler_job",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="恢复一个定时任务",
)
async def _(payload: SchedulerJobKey) -> Result:
    try:
        controller, name = _controller(payload)
        controller.resume_job(name)
        return Result.ok(info=f"已恢复 `{payload.service}:{name}`")
    except LookupError as e:
        return Result.fail(str(e), 404)
    except BotRuntimeError as e:
        return Result.fail(str(e), 400)
    except Exception as e:
        log.error(f"{router.prefix}/resume_scheduler_job 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/run_scheduler_job",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="立即执行一次定时任务（不改变它原本的暂停状态）",
)
async def _(payload: SchedulerJobKey) -> Result:
    try:
        controller, name = _controller(payload)
        controller.run_job_now(name)
        return Result.ok(info=f"已触发 `{payload.service}:{name}`（只跑这一次）")
    except LookupError as e:
        return Result.fail(str(e), 404)
    except BotRuntimeError as e:
        return Result.fail(str(e), 400)
    except Exception as e:
        log.error(f"{router.prefix}/run_scheduler_job 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


__all__ = ["SchedulerJobInfo", "SchedulerJobKey", "router"]
