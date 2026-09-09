from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ATRI.exceptions import str_traceback
from ATRI.log import log
from ATRI.system.plugin_store.data_source import PluginManager

from ....base_model import Result
from ....utils import authentication
from .model import PluginRequest

router = APIRouter(prefix="/store")


@router.get(
    "/get_plugin_store",
    dependencies=[authentication()],
    response_model=Result[dict],
    response_class=JSONResponse,
    description="获取插件商店插件信息",  # type: ignore
)
async def _() -> Result[dict]:
    try:
        # 获取插件列表
        plugin_data = await PluginManager.get_plugin_list()
        # 获取已安装的模块列表
        from ATRI.service import ServiceTools

        plugins = list(ServiceTools.service_list.keys())
        # 转换插件数据格式
        plugin_list = []
        for idx, (plugin_name, plugin_info) in enumerate(plugin_data.items()):
            p_version = plugin_info.get("version", "unknown")
            local_version = None
            remote_version = None
            if p_version == "github":
                try:
                    meta = await PluginManager.get_github_plugin_meta(plugin_info["repo"])
                    p_version = meta.get("version", "unknown")
                except Exception:
                    p_version = "error"
            update_versions = await PluginManager.check_update(plugin_name)
            if update_versions is not None:
                local_version, remote_version = update_versions
            plugin_list.append(
                {
                    "id": idx,
                    "name": plugin_name,
                    "author": plugin_info.get("author", "未知"),
                    "version": p_version,
                    "plugin_type": plugin_info.get("type", "其他插件"),
                    "description": plugin_info.get("docs", ""),
                    "github_url": plugin_info.get("repo", ""),
                    "need_update": update_versions is not None,
                    "local_version": local_version,
                    "remote_version": remote_version,
                }
            )
        return Result.ok({"install_plugin": plugins, "plugin_list": plugin_list})
    except Exception as e:
        str_tb = str_traceback(e)
        log.error(f"获取插件商店插件信息失败:{str_tb}")
        return Result.fail(f"获取插件商店插件信息失败：{type(e).__name__}: {e}")


@router.post(
    "/install_plugin",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="安装插件",  # type: ignore
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
    description="更新插件",  # type: ignore
)
async def _(request: PluginRequest) -> Result:
    try:
        plugin_name = request.service
        ok, msg = await PluginManager.update_plugin(plugin_name)
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
    description="移除插件",  # type: ignore
)
async def _(request: PluginRequest) -> Result:
    try:
        await PluginManager.remove_plugin(request.service)
        return Result.ok(info=f"插件 {request.service} 移除成功")
    except Exception as e:
        str_tb = str_traceback(e)
        log.error(f"移除插件失败:{str_tb}")
        return Result.fail(f"移除插件失败：{type(e).__name__}: {e}")
