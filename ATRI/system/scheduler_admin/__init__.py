from nonebot.adapters import Message
from nonebot.params import CommandArg

from ATRI.exceptions import BotRuntimeError
from ATRI.permission import MASTER
from ATRI.service import Service

from .data_source import describe_jobs, resolve_job

plugin = (
    Service(
        "调度管理",
        "查看与管理各服务的定时任务",
        "0.1.0",
        Service.ServiceType.SYSTEM,
    )
    .allow_switch(False)
    .permission(MASTER)
)

job_list = plugin.on_command("任务列表", "列出定时任务，可附带服务名过滤")


@job_list.handle()
async def _(args: Message = CommandArg()):
    service = args.extract_plain_text().strip() or None
    await job_list.finish(describe_jobs(service))


job_pause = plugin.on_command("任务暂停", "暂停指定任务（服务:任务名）")


@job_pause.handle()
async def _(args: Message = CommandArg()):
    try:
        controller, name = resolve_job(args.extract_plain_text())
        controller.pause_job(name)
    except (LookupError, BotRuntimeError) as e:
        await job_pause.finish(str(e))
    await job_pause.finish(f"已暂停 `{controller.service}:{name}`")


job_resume = plugin.on_command("任务恢复", "恢复指定任务（服务:任务名）")


@job_resume.handle()
async def _(args: Message = CommandArg()):
    try:
        controller, name = resolve_job(args.extract_plain_text())
        controller.resume_job(name)
    except (LookupError, BotRuntimeError) as e:
        await job_resume.finish(str(e))
    await job_resume.finish(f"已恢复 `{controller.service}:{name}`")


job_run = plugin.on_command("任务执行", "立即执行一次指定任务（服务:任务名）")


@job_run.handle()
async def _(args: Message = CommandArg()):
    try:
        controller, name = resolve_job(args.extract_plain_text())
        controller.run_job_now(name)
    except (LookupError, BotRuntimeError) as e:
        await job_run.finish(str(e))
    await job_run.finish(f"已安排 `{controller.service}:{name}` 立即执行")
