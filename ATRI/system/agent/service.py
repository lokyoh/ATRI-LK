from ATRI.permission import MASTER
from ATRI.service import Service

plugin = (
    Service(
        "agent",
        "为ATRI-LK项目专门设计的智能体。",
        "0.1.0",
        Service.ServiceType.SYSTEM,
    )
    .allow_switch(False)
    .main_cmd("agent")
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


def init():
    from .llm import load_models_from_config

    load_models_from_config()


plugin.on_startup(init)
