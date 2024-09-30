import os
import shutil
import subprocess
from pathlib import Path

import nonebot

from ATRI.log import log
from ATRI.service import service_list
from ATRI.utils import request
from ATRI.exceptions import PluginError

PLUGINS_URL = "https://raw.githubusercontent.com/lokyoh/ATRI-LK-plugin/main/plugin.json"
FILE_URL = "https://api.github.com/repos/lokyoh/ATRI-LK-plugin/contents/{}?ref=main"
PLUGINS_DIR = Path(".") / "plugins"


class PluginManager:
    plugin_list = {}

    @classmethod
    async def fresh_list(cls):
        log.debug("更新插件列表信息")
        data = await request.get(PLUGINS_URL)
        cls.plugin_list = data.json()

    @classmethod
    async def get_plugin_list(cls) -> dict:
        try:
            await cls.fresh_list()
        except Exception:
            log.warning("更新插件列表失败")
            pass
        return cls.plugin_list

    @classmethod
    async def check_list(cls):
        if len(cls.plugin_list) == 0:
            try:
                await cls.fresh_list()
            except Exception:
                log.warning("更新插件列表失败")
                raise PluginError("更新列表失败")

    @classmethod
    async def install_plugin(cls, plugin_name: str, load: bool = False):
        if not plugin_name in cls.plugin_list:
            log.info(f"安装未知插件`{plugin_name}`")
            raise PluginError(f"找不到插件 {plugin_name}")
        _plugin = cls.plugin_list[plugin_name]
        res_list = _plugin.get("res")
        try:
            if res_list:
                log.info(f"开始为插件`{plugin_name}`下载资源")
                for res in res_list:
                    data = await request.get(FILE_URL.format(res))
                    if data.status_code != 200:
                        log.warning(f"在下载插件`{plugin_name}`的资源时网络连接错误，code:{data.status_code}")
                        raise PluginError(f"网络连接错误，code:{data.status_code}")
                    if '.' in res:
                        file = data.json()
                        file_list = [file]
                    else:
                        file_list = data.json()
                    for file in file_list:
                        file_path = Path(".") / file["path"]
                        data = await request.get(file["download_url"])
                        file_path.parent.mkdir(parents=True, exist_ok=True)
                        log.debug(f"下载文件`{file_path}`")
                        with open(file_path, 'wb') as f:
                            f.write(data.content)
        except Exception as e:
            log.warning(f"插件`{plugin_name}`资源安装失败:发生错误{e}")
            raise PluginError(f"插件资源安装失败:{e}")
        path = _plugin["path"]
        p_path = path.replace(".", "/")
        try:
            log.debug(f"开始下载插件`{plugin_name}`")
            if _plugin["is_dir"]:
                data = await request.get(FILE_URL.format(p_path))
                if data.status_code != 200:
                    raise PluginError(f"网络连接错误,code:{data.status_code}")
                file_list = data.json()
            else:
                p_path += ".py"
                data = await request.get(FILE_URL.format(p_path))
                if data.status_code != 200:
                    raise PluginError(f"网络连接错误,code:{data.status_code}")
                file = data.json()
                file_list = [file]
            for file in file_list:
                file_path = Path(".") / file["path"]
                data = await request.get(file["download_url"])
                file_path.parent.mkdir(parents=True, exist_ok=True)
                log.debug(f"下载文件`{file_path}`")
                with open(file_path, 'w', encoding="utf-8") as f:
                    f.write(data.text)
            req_path = Path('.') / p_path / "requirements.txt"
            if req_path.exists():
                log.info(f"开始为`{plugin_name}`安装依赖")
                result = subprocess.run(
                    ["pip", "install", "-r", str(req_path)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                log.debug(f"`{plugin_name}`依赖安装信息:{result}")
            if load:
                nonebot.load_plugin(path)
            log.info(f"插件`{plugin_name}`安装结束")
        except Exception as e:
            log.warning(f"插件`{plugin_name}`安装失败:发生错误{e}")
            raise PluginError(f"插件安装失败:{e}")

    @classmethod
    async def remove_plugin(cls, plugin_name: str):
        if not plugin_name in service_list:
            raise PluginError(f"未安装插件 {plugin_name}")
        if not plugin_name in cls.plugin_list:
            raise PluginError(f"无法卸载 {plugin_name}，请手动卸载")
        path = cls.plugin_list[plugin_name]["path"].replace(".", "/")
        if not cls.plugin_list[plugin_name]["is_dir"]:
            path += ".py"
            os.remove(path)
        else:
            shutil.rmtree(path)
        log.info(f"插件`{plugin_name}`移除成功")
