from ATRI.permission import MASTER
from ATRI.service import Service

plugin = (
    Service(
        "agent",
        "为ATRI-LK项目专门设计的智能体。",
        "0.4.1",
        Service.ServiceType.SYSTEM,
    )
    .allow_switch(False)
    .main_cmd("agent")
    .permission(MASTER)
)

reload = plugin.cmd_as_group("reload", "重新加载配置", permission=MASTER)


@reload.handle()
async def _():
    from . import reload_all

    reload_all()
    await reload.finish("已重新加载agent配置")


summarize_memory = plugin.cmd_as_group("总结记忆", "总结记忆", permission=MASTER)


@summarize_memory.handle()
async def _():
    from ATRI.system.agent.agent.memory.manage import summarize_memories

    await summarize_memories()
    await summarize_memory.finish("总结记忆完成")


async def init():
    from ATRI.log import log

    from .agent.daily_task import init as init_daily_task
    from .agent.function_calling import register_function_calling
    from .agent.initiative_start import register_patrol
    from .agent.task import restore_agent_tasks
    from .config import apply_runtime_config
    from .llm import load_models_from_config
    from .llm.log import init_llm_log

    init_llm_log()
    load_models_from_config()
    register_function_calling()
    apply_runtime_config()
    init_daily_task()
    register_patrol(plugin)
    try:
        await restore_agent_tasks()
    except Exception as exc:
        log.warning(f"恢复 Agent 定时任务失败：{exc}")


plugin.on_startup(init)
