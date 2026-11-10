from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ATRI.exceptions import str_traceback
from ATRI.log import log
from ATRI.system.agent.agent import task as task_mod

from ....base_model import Result
from ....utils import authentication

router = APIRouter()


class TaskInfo(BaseModel):
    id: str
    """任务 id（12 位十六进制）"""
    name: str
    """任务名（用户描述，超长会被截到 30 字）"""
    kind: str
    """once / cron / interval"""
    kind_label: str = ""
    """类型的中文说法，如「一次性」「每天」"""
    message: str
    """到点时给她自己看的提醒内容"""
    chat_id: str
    """目标会话（群号 / group_openid）"""
    bot_id: str = ""
    """创建它的那个 bot。空表示无去向——这类任务到点不会执行，会被作废"""
    user_id: str = ""
    """委托人"""
    when: str
    """触发时间的人话描述，如「9 分钟后」「每天 08:00」"""
    run_at: float = 0
    """一次性任务的触发时间戳（UTC 秒）"""
    spec: dict = {}
    """周期任务的触发器参数"""
    created_at: float = 0
    """创建时间戳（UTC 秒）"""


def _to_info(task: dict) -> TaskInfo:
    return TaskInfo(
        id=str(task.get("id") or ""),
        name=str(task.get("name") or ""),
        kind=str(task.get("kind") or ""),
        kind_label=task_mod.KIND_LABEL.get(str(task.get("kind") or ""), ""),
        message=str(task.get("message") or ""),
        chat_id=str(task.get("chat_id") or ""),
        bot_id=str(task.get("bot_id") or ""),
        user_id=str(task.get("user_id") or ""),
        when=task_mod.when_text(task),
        run_at=float(task.get("run_at") or 0),
        spec=dict(task.get("spec") or {}),
        created_at=float(task.get("created_at") or 0),
    )


@router.get(
    "/get_tasks",
    dependencies=[authentication()],
    response_model=Result[list[TaskInfo]],
    response_class=JSONResponse,
    description="获取 Agent 定时任务列表（全部 bot、全部会话）",
)
async def _() -> Result[list[TaskInfo]]:
    try:
        tasks = await task_mod.list_tasks()
        return Result.ok([_to_info(t) for t in tasks], "拿到信息啦!")
    except Exception as e:
        log.error(f"{router.prefix}/get_tasks 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/cancel_task",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="取消一个 Agent 定时任务",
)
async def _(task_id: str) -> Result:
    try:
        # 不传 chat_id / bot_id：WebUI 是管理端，不必受会话归属限制。
        # 但任务必须真的存在——避免删掉不属于 agent 的调度器作业。
        task = await task_mod.get_task(task_id)
        if task is None:
            return Result.fail(f"任务 {task_id} 不存在（可能刚执行完）", 404)
        if not await task_mod.cancel(task_id):
            return Result.fail(f"取消任务 {task_id} 失败")
        return Result.ok(info="取消成功!")
    except Exception as e:
        log.error(f"{router.prefix}/cancel_task 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")
