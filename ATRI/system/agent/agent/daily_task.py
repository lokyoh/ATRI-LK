from ATRI.event import Priority, daily_update


@daily_update(priority=Priority.LOW)
async def agent_daily_task():
    from .memory.manage import summarize_memories
    from .schedule import generate_schedule

    await summarize_memories()
    await generate_schedule()


def init():
    pass
