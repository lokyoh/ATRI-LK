from typing import Any, Dict

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from ATRI.exceptions import str_traceback
from ATRI.log import log
from ATRI.service import ServiceTools
from ATRI.utils.package_manager import PackageManager

from ....base_model import Result
from ....utils import authentication
from .data_source import ApiDataSource
from .model import (
    InstallDependenciesPayload,
    PluginCount,
    PluginDetail,
    PluginInfo,
    PluginSwitch,
    UpdatePlugin,
)

router = APIRouter(prefix="/plugin")


@router.get(
    "/get_plugin_list",
    dependencies=[authentication()],
    response_model=Result[list[PluginInfo]],
    response_class=JSONResponse,
    description="获取插件列表",  # type: ignore
)
async def _(plugin_type: list = Query(None)) -> Result[list[PluginInfo]]:
    try:
        result = await ApiDataSource.get_plugin_list(plugin_type)
        return Result.ok(result, "拿到信息啦!")
    except Exception as e:
        log.error(f"{router.prefix}/get_plugin_list 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.get(
    "/get_plugin_count",
    dependencies=[authentication()],
    response_model=Result[PluginCount],
    response_class=JSONResponse,
    description="获取插件数量",  # type: ignore
)
async def _() -> Result[PluginCount]:
    try:
        plugin_count = await ApiDataSource.get_plugin_count()
        return Result.ok(plugin_count, "拿到信息啦!")
    except Exception as e:
        log.error(f"{router.prefix}/get_plugin_count 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/update_plugin",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="更新插件参数",
)
async def _(param: UpdatePlugin) -> Result:
    try:
        await ApiDataSource.update_plugin(param)
        return Result.ok(info="已经帮你写好啦!")
    except (ValueError, KeyError):
        return Result.fail("插件数据不存在...")
    except Exception as e:
        log.error(f"{router.prefix}/update_plugin 调用错误:{str_traceback(e)}")
        return Result.fail(f"{type(e)}: {e}")


@router.post(
    "/change_switch",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="开关插件",
)
async def _(param: PluginSwitch) -> Result:
    try:
        s = ServiceTools(param.service)
        config = s.load_service_config()
        config.enabled = param.status
        s.save_service_config(config)
        return Result.ok(info="成功改变了开关状态!")
    except Exception as e:
        log.error(f"{router.prefix}/change_switch 调用错误:{str_traceback(e)}")
        return Result.fail(f"{type(e)}: {e}")


@router.get(
    "/get_plugin",
    dependencies=[authentication()],
    response_model=Result[PluginDetail],
    response_class=JSONResponse,
    description="获取插件详情",
)
async def _(service: str) -> Result[PluginDetail]:
    try:
        detail = await ApiDataSource.get_plugin_detail(service)
        return Result.ok(detail, "获取成功!")
    except (ValueError, KeyError):
        return Result.fail("插件数据不存在...")
    except Exception as e:
        log.error(f"{router.prefix}/get_plugin 调用错误:{str_traceback(e)}")
        return Result.fail(f"{type(e)}: {e}")


@router.post(
    "/install_dependencies",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="安装/卸载依赖",
)
async def _(payload: InstallDependenciesPayload) -> Result:
    try:
        if not payload.dependencies:
            return Result.fail("依赖列表不能为空")
        if payload.handle_type == "install":
            _, result = PackageManager().install(payload.dependencies)
        else:
            _, result = PackageManager().uninstall(payload.dependencies)
        return Result.ok(result)
    except Exception as e:
        log.error(f"{router.prefix}/install_dependencies 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.get(
    "/get_plugin_config",
    dependencies=[authentication()],
    response_model=Result[Dict[str, Any]],
    response_class=JSONResponse,
    description="获取插件详情",
)
async def _(service: str) -> Result[Dict[str, Any]]:
    try:
        if service not in ServiceTools.service_list:
            raise ValueError("插件不存在")
        s = ServiceTools.service_list[service]
        p_c = s.plugin_config()
        if p_c is None:
            return Result.fail("插件配置不存在")
        detail = p_c.config().model_dump()
        return Result.ok(detail, "获取成功!")
    except (ValueError, KeyError):
        return Result.fail("插件数据不存在...")
    except Exception as e:
        log.error(f"{router.prefix}/get_plugin 调用错误:{str_traceback(e)}")
        return Result.fail(f"{type(e)}: {e}")


@router.post(
    "/update_plugin_config",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="更新插件参数",
)
async def _(param: UpdatePlugin) -> Result:
    try:
        if param.service not in ServiceTools.service_list:
            raise ValueError("插件不存在")
        s = ServiceTools.service_list[param.service]
        p_c = s.plugin_config()
        if p_c is None:
            return Result.fail("插件配置不存在")
        config = p_c.model.model_validate(param.configs)
        p_c.change_config(config)
        return Result.ok(info="已经帮你写好啦!")
    except (ValueError, KeyError):
        return Result.fail("插件数据不存在...")
    except Exception as e:
        log.error(f"{router.prefix}/update_plugin 调用错误:{str_traceback(e)}")
        return Result.fail(f"{type(e)}: {e}")
