from nonebot.adapters import Bot, Event, Message
from nonebot.params import CommandArg

from ATRI.bot import BotUtils
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


def _chat_of(event: Event) -> str:
    """这个事件属于哪个会话。定时任务按群记，私聊里没有。"""
    return str(getattr(event, "group_id", "") or "")


def _cmd(name: str) -> str:
    """把管理指令写成用户可以直接敲的样子。"""
    return f"{BotUtils.get_command_start()}{name}"


agent_tasks = plugin.on_command(
    "agent.任务列表",
    "查看本机器人在本会话的定时任务",
    aliases={"agent.定时任务"},
    permission=MASTER,
)


@agent_tasks.handle()
async def cmd_list_tasks(bot: Bot, event: Event):
    from .agent import task as task_mod

    chat_id = _chat_of(event)
    if not chat_id:
        await agent_tasks.finish("定时任务是按群记的，私聊里没有")
    self_id = str(bot.self_id)
    mine = await task_mod.list_tasks(chat_id, self_id)
    others = len(await task_mod.list_tasks(chat_id)) - len(mine)
    if not mine:
        text = f"本会话里我（{self_id}）没有定时任务"
        if others:
            text += (
                f"\n另有 {others} 个不是我安排的"
                "（别的机器人 / 没记来源的旧任务），我动不了"
            )
        await agent_tasks.finish(text)
    lines = [f"我在本会话有 {len(mine)} 个定时任务（{self_id}）："]
    lines += [f"{i}. {task_mod.describe_line(t)}" for i, t in enumerate(mine, 1)]
    if others:
        lines.append(f"另有 {others} 个不是我安排的，未列出")
    lines.append(
        f"取消：{_cmd('agent.取消任务')} <id>　全部取消：{_cmd('agent.清空任务')}"
    )
    await agent_tasks.finish("\n".join(lines))


agent_cancel_task = plugin.on_command(
    "agent.取消任务",
    "取消本机器人在本会话的一个定时任务",
    permission=MASTER,
)


@agent_cancel_task.handle()
async def cmd_cancel_task(bot: Bot, event: Event, args: Message = CommandArg()):
    from .agent import task as task_mod

    chat_id = _chat_of(event)
    if not chat_id:
        await agent_cancel_task.finish("定时任务是按群记的，私聊里没有")
    raw = args.extract_plain_text().strip()
    if not raw:
        await agent_cancel_task.finish(
            f"要取消哪个任务？先看看 {_cmd('agent.任务列表')}"
        )
    self_id = str(bot.self_id)
    tasks = await task_mod.list_tasks(chat_id, self_id)
    exact = [t for t in tasks if str(t.get("id", "")) == raw]
    matches = exact or [t for t in tasks if str(t.get("id", "")).startswith(raw)]
    if not matches:
        await agent_cancel_task.finish(f"我这里没有 id 是 {raw} 的任务")
    if len(matches) > 1:
        ids = "、".join(str(t.get("id", "")) for t in matches)
        await agent_cancel_task.finish(f"{raw} 开头的任务有好几个：{ids}，补全一点")
    task = matches[0]
    task_id = str(task.get("id", ""))
    if not await task_mod.cancel(task_id, chat_id, self_id):
        await agent_cancel_task.finish(f"取消 {task_id} 失败，可能刚刚已经执行完了")
    await agent_cancel_task.finish(f"已取消 {task_id}「{task.get('message', '')}」")


agent_clear_tasks = plugin.on_command(
    "agent.清空任务",
    "清空本机器人在本会话的全部定时任务",
    permission=MASTER,
)


@agent_clear_tasks.handle()
async def cmd_clear_tasks(bot: Bot, event: Event):
    from .agent import task as task_mod

    chat_id = _chat_of(event)
    if not chat_id:
        await agent_clear_tasks.finish("定时任务是按群记的，私聊里没有")
    self_id = str(bot.self_id)
    tasks = await task_mod.list_tasks(chat_id, self_id)
    if not tasks:
        await agent_clear_tasks.finish("本会话里没有我的定时任务")
    cleared = 0
    for task in tasks:
        if await task_mod.cancel(str(task.get("id", "")), chat_id, self_id):
            cleared += 1
    if not cleared:
        await agent_clear_tasks.finish("一个也没清掉，可能刚刚都执行完了")
    await agent_clear_tasks.finish(f"已清空我在本会话的 {cleared} 个定时任务")


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
