import asyncio

import nonebot
from fastapi import APIRouter, FastAPI

from ATRI import driver
from ATRI.exceptions import str_traceback
from ATRI.log import _LOG_FORMAT, LoguruNameDealer, log
from ATRI.service import Service

from .api.configure import router as configure_router
from .api.logs import router as ws_log_routes
from .api.logs.log_manager import LOG_STORAGE
from .api.menu import router as menu_router
from .api.tabs.agent import router as agent_router
from .api.tabs.dashboard import router as dashboard_router
from .api.tabs.main import router as main_router
from .api.tabs.main import ws_router as status_routes
from .api.tabs.manage import router as manage_router
from .api.tabs.manage.chat import ws_router as chat_routes
from .api.tabs.plugin_manage import router as plugin_router
from .api.tabs.plugin_manage.store import router as store_router
from .auth import router as auth_router
from .public import init_public

plugin = Service(
    service="WebAPI",
    docs="ATRI的webapi",
    version="0.0.4",
    type_=Service.ServiceType.HIDDEN,
)

driver = driver()

BaseApiRouter = APIRouter(prefix="/atri/api")

BaseApiRouter.include_router(auth_router)
BaseApiRouter.include_router(store_router)
BaseApiRouter.include_router(dashboard_router)
BaseApiRouter.include_router(main_router)
BaseApiRouter.include_router(manage_router)
BaseApiRouter.include_router(plugin_router)
BaseApiRouter.include_router(menu_router)
BaseApiRouter.include_router(configure_router)
BaseApiRouter.include_router(agent_router)

WsApiRouter = APIRouter(prefix="/atri/socket")

WsApiRouter.include_router(status_routes)
WsApiRouter.include_router(ws_log_routes)
WsApiRouter.include_router(chat_routes)


@driver.on_startup
async def _():
    try:
        # 存储任务引用的列表，防止任务被垃圾回收
        _tasks = []

        async def log_sink(message: str):
            loop = None
            if not loop:
                try:
                    loop = asyncio.get_running_loop()
                except Exception as e:
                    log.warning(str_traceback(e))
            if not loop:
                loop = asyncio.new_event_loop()
            # 存储任务引用到外部列表中
            _tasks.append(loop.create_task(LOG_STORAGE.add(message.rstrip("\n"))))

        log.add(log_sink, colorize=True, filter=LoguruNameDealer(), format=_LOG_FORMAT)

        app: FastAPI = nonebot.get_app()
        app.include_router(BaseApiRouter)
        app.include_router(WsApiRouter)
        await init_public(app)
        log.info("API启动成功")
    except Exception as e:
        log.error(f"API启动失败:{str_traceback(e)}")
