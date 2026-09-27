from ATRI.event import AsyncEventBus, logging_middleware

AgentEventBus = AsyncEventBus("AgentEventBus")
AgentEventBus.use(logging_middleware)
