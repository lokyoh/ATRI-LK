from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ATRI.exceptions import str_traceback
from ATRI.log import log
from ATRI.service import ServiceTools
from ATRI.system.plugin_store import progress
from ATRI.system.plugin_store.compat import lkapi_note
from ATRI.system.plugin_store.data_source import PluginEntry, PluginManager
from ATRI.version import compat_range, framework_version, lkapi_version

from ....base_model import Result
from ....utils import authentication
from .model import PluginRequest

router = APIRouter(prefix="/store")


@router.post(
    "/refresh_plugin_store",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="强制从商店索引刷新插件列表",
)
async def _() -> Result:
    try:
        plugin_list = await PluginManager.refresh_list()
        return Result.ok(info=f"已刷新，共 {len(plugin_list)} 个插件")
    except Exception as e:
        log.error(f"{router.prefix}/refresh_plugin_store 调用错误:{str_traceback(e)}")
        return Result.fail(f"刷新失败：{type(e).__name__}: {e}")


@router.get(
    "/get_store_progress",
    dependencies=[authentication()],
    response_model=Result[list],
    response_class=JSONResponse,
    description="获取插件安装/更新进度",
)
async def _() -> Result[list]:
    try:
        return Result.ok(progress.snapshot(), "拿到进度啦!")
    except Exception as e:
        log.error(f"{router.prefix}/get_store_progress 调用错误:{str_traceback(e)}")
        return Result.fail(f"获取进度失败：{type(e).__name__}: {e}")


@router.get(
    "/get_plugin_store",
    dependencies=[authentication()],
    response_model=Result[dict],
    response_class=JSONResponse,
    description="获取插件商店插件信息",
)
async def _() -> Result[dict]:
    try:
        # 获取插件列表
        plugin_data = await PluginManager.get_plugin_list()
        # 获取已安装的模块列表
        plugins = list(ServiceTools.service_list.keys())
        # 转换插件数据格式
        plugin_list = []
        for idx, (plugin_name, plugin_info) in enumerate(plugin_data.items()):
            raw_info = dict(plugin_info)
            local_meta = PluginManager.read_local_meta(plugin_name, raw_info)
            if local_meta:
                raw_info = {
                    **raw_info,
                    **{k: v for k, v in local_meta.items() if v is not None},
                    "repo": raw_info.get("repo") or local_meta.get("repo") or "",
                }
            entry = PluginEntry.from_mapping(plugin_name, raw_info)
            p_version = entry.version
            if p_version == "github":
                p_version = "unknown"
            update_versions = await PluginManager.check_update(plugin_name)
            local_version = None
            remote_version = None
            if update_versions is not None:
                local_version, remote_version = update_versions
            compat = PluginManager.compat(entry)
            requirement = entry.requirement
            plugin_list.append(
                {
                    "id": idx,
                    "name": plugin_name,
                    "author": entry.author or "未知",
                    "version": p_version,
                    "plugin_type": requirement.type,
                    "description": entry.docs,
                    "github_url": entry.repo,
                    "need_update": update_versions is not None,
                    "local_version": local_version,
                    "remote_version": remote_version,
                    "require_framework": requirement.framework,
                    "require_lkapi": requirement.lkapi,
                    "framework_version": compat.framework,
                    "lkapi_version": compat.lkapi,
                    "supported_framework": compat.supported,
                    "compatible": compat.ok,
                    "compat_status": compat.status,
                    "compat_label": compat.label,
                    "compat_reason": compat.reason,
                    "lkapi_note": lkapi_note(requirement),
                }
            )
        return Result.ok(
            {
                "install_plugin": plugins,
                "plugin_list": plugin_list,
                "framework_version": framework_version(),
                "supported_framework": compat_range(),
                "lkapi_version": lkapi_version(),
            }
        )
    except Exception as e:
        str_tb = str_traceback(e)
        log.error(f"获取插件商店插件信息失败:{str_tb}")
        return Result.fail(f"获取插件商店插件信息失败：{type(e).__name__}: {e}")


@router.post(
    "/install_plugin",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="安装插件",
)
async def _(request: PluginRequest) -> Result:
    try:
        # 安装插件
        await PluginManager.install_plugin(request.service, load=True)
        return Result.ok(info=f"插件 {request.service} 安装成功")
    except Exception as e:
        str_tb = str_traceback(e)
        log.error(f"安装插件失败:{str_tb}")
        return Result.fail(f"安装插件失败：{type(e).__name__}: {e}")


@router.post(
    "/update_plugin",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="更新插件",
)
async def _(request: PluginRequest) -> Result:
    try:
        plugin_name = request.service
        ok, msg = await PluginManager.update_plugin(
            plugin_name, force=bool(request.force)
        )
        if ok:
            return Result.ok(info=msg)
        else:
            return Result.fail(info=msg)
    except Exception as e:
        str_tb = str_traceback(e)
        log.error(f"更新插件失败:{str_tb}")
        return Result.fail(f"更新插件失败：{type(e).__name__}: {e}")


@router.post(
    "/remove_plugin",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="移除插件",
)
async def _(request: PluginRequest) -> Result:
    try:
        await PluginManager.remove_plugin(request.service)
        return Result.ok(info=f"插件 {request.service} 移除成功")
    except Exception as e:
        str_tb = str_traceback(e)
        log.error(f"移除插件失败:{str_tb}")
        return Result.fail(f"移除插件失败：{type(e).__name__}: {e}")
