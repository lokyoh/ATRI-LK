import os
import shutil
from pathlib import Path

import nonebot

from ATRI.service import service_list
from ATRI.utils import request
from ATRI.exceptions import PluginError

PLUGINS_URL = "https://raw.githubusercontent.com/lokyoh/ATRI-LK-plugin/main/plugin.json"
PLUGINS_CDN_URL = "https://cdn.jsdelivr.net/gh/lokyoh/ATRI-LK-plugin@main/plugin.json"
FILE_URL = "https://api.github.com/repos/lokyoh/ATRI-LK-plugin/contents/{}?ref=main"
PLUGINS_DIR = Path(".") / "plugins"


class PluginManager:
    plugin_list = {}

    @classmethod
    async def fresh_list(cls):
        data = await request.get(PLUGINS_URL)
        cls.plugin_list = data.json()

    @classmethod
    async def get_plugin_list(cls) -> dict:
        try:
            await cls.fresh_list()
        except Exception:
            pass
        return cls.plugin_list

    @classmethod
    async def check_list(cls):
        if len(cls.plugin_list) == 0:
            try:
                await cls.fresh_list()
            except Exception:
                raise PluginError("更新列表失败")

    @classmethod
    async def install_plugin(cls, plugin_name: str, load: bool = False):
        if not plugin_name in cls.plugin_list:
            raise PluginError(f"找不到插件 {plugin_name}")
        _plugin = cls.plugin_list[plugin_name]
        path = _plugin["path"]
        p_path = path.replace(".", "/")
        try:
            if _plugin["is_dir"]:
                data = await request.get(FILE_URL.format(p_path))
                file_list = data.json()
            else:
                p_path += ".py"
                data = await request.get(FILE_URL.format(p_path))
                file = data.json()
                file_list = [file]
            for file in file_list:
                file_path = Path(".") / file["path"]
                data = await request.get(file["download_url"])
                file_path.parent.mkdir(parents=True, exist_ok=True)
                with open(file_path, 'w', encoding="utf-8") as f:
                    f.write(data.text)
            if load:
                nonebot.load_plugin(path)
        except Exception as e:
            raise PluginError(f"安装失败:{e}")

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