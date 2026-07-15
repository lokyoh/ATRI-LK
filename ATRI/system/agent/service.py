from ATRI.permission import MASTER
from ATRI.service import Service

plugin = (
    Service(
        "agent",
        "为ATRI-LK项目专门设计的智能体。",
        "0.0.2",
        Service.ServiceType.HIDDEN,
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


def init():
    from .llm import load_models_from_config

    load_models_from_config()


plugin.on_startup(init)
