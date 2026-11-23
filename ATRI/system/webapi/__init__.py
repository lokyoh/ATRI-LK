import asyncio

import nonebot
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute

from ATRI import conf, driver
from ATRI.exceptions import str_traceback
from ATRI.log import _LOG_FORMAT, LoguruNameDealer, log
from ATRI.service import Service

from .api.configure import router as configure_router
from .api.logs import router as ws_log_routes
from .api.logs.history import router as history_router
from .api.logs.log_manager import LOG_STORAGE, LogEntry
from .api.menu import router as menu_router
from .api.tabs.agent import router as agent_router
from .api.tabs.dashboard import router as dashboard_router
from .api.tabs.lkbuser import router as lkbot_user_router
from .api.tabs.main import router as main_router
from .api.tabs.main import ws_router as status_routes
from .api.tabs.manage import router as manage_router
from .api.tabs.plugin_manage import router as plugin_router
from .api.tabs.plugin_manage.store import router as store_router
from .auth import router as auth_router
from .public import init_public

plugin = Service(
    service="WebAPI",
    docs="ATRI的webapi",
    version="0.0.10",
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
BaseApiRouter.include_router(lkbot_user_router)
BaseApiRouter.include_router(history_router)

WsApiRouter = APIRouter(prefix="/atri/socket")

WsApiRouter.include_router(status_routes)
WsApiRouter.include_router(ws_log_routes)


@driver.on_startup
async def _():
    try:
        # 日志存储 sink：同步写入 LOG_STORAGE（监听器只做入队，无 await、无 task），
        # 不再为每条日志创建并永久持有 task（旧实现 _tasks 只增不减，是内存泄漏）
        LOG_STORAGE.bind_loop(asyncio.get_running_loop())

        def log_sink(message):
            record = message.record
            LOG_STORAGE.add(
                LogEntry(
                    time=record["time"],
                    level=record["level"].name,
                    text=str(message).rstrip("\n"),
                )
            )

        log.add(
            log_sink,
            # 级别与主输出保持一致：生产环境不再把 DEBUG 灌进 WebUI
            level="DEBUG" if conf.BotConfig.debug else "INFO",
            colorize=True,
            filter=LoguruNameDealer(),
            format=_LOG_FORMAT,
        )

        app: FastAPI = nonebot.get_app()
        app.include_router(BaseApiRouter)
        app.include_router(WsApiRouter)
        await init_public(app)
        _audit_route_auth(app)
        log.info("API启动成功")
    except Exception as e:
        log.error(f"API启动失败:{str_traceback(e)}")


# 这些端点刻意公开：登录、存活探测、退出登录（自身凭吊销语义保护）
_PUBLIC_API_PATHS = {"/atri/api/login", "/atri/api/ping", "/atri/api/logout"}


def _audit_route_auth(app: FastAPI) -> None:
    """启动自检：枚举已注册的 HTTP 路由，未挂任何鉴权依赖的给出警告。

    用于防止以后新增端点时漏挂 authentication()（WS 路由由握手鉴权单独覆盖，
    不在此列）。
    """
    unprotected = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path in _PUBLIC_API_PATHS:
            continue
        # dependant.dependencies 同时覆盖路由级与参数级 Depends
        if not route.dependant.dependencies:
            unprotected.append(f"{','.join(route.methods)} {route.path}")
    if unprotected:
        log.warning(f"以下 API 路由未挂鉴权依赖，请确认是否有意公开: {unprotected}")
