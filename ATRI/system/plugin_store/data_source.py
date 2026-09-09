import asyncio
import hashlib
import importlib
import locale
import os
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import anyio
import httpx
import nonebot
import yaml

from ATRI.exceptions import PluginError
from ATRI.load import package_requirements, parse_requirement_line
from ATRI.log import log
from ATRI.service import ServiceTools
from ATRI.utils import request
from ATRI.utils.package_manager import PackageManager

PLUGINS_URL = "https://raw.githubusercontent.com/lokyoh/ATRI-LK-plugin/main/plugin.json"
FILE_URL = "https://api.github.com/repos/lokyoh/ATRI-LK-plugin/contents/{}?ref=main"
PLUGINS_DIR = Path(".") / "plugins"
DOWNLOAD_RETRIES = 3
DOWNLOAD_RETRY_DELAY = 1


def _build_shell_command(args: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(args)
    return shlex.join(args)


async def _run_shell_command(command: str, cwd: str | Path | None = None):
    process = await asyncio.create_subprocess_shell(
        command,
        cwd=str(cwd) if cwd is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await process.communicate()
    encoding = locale.getpreferredencoding(False)
    stdout = stdout_bytes.decode(encoding, errors="replace")
    stderr = stderr_bytes.decode(encoding, errors="replace")
    if process.returncode != 0:
        raise subprocess.CalledProcessError(
            process.returncode,
            command,
            output=stdout,
            stderr=stderr,
        )
    return stdout, stderr


def _safe_rmtree(path: Path):
    """
    在 Windows 上更稳健地删除目录，处理只读文件和文件占用问题
    """
    import time

    if not path.exists():
        return

    def _remove_readonly(func, path, excinfo):
        """错误处理函数：移除只读属性后重试"""
        try:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            func(path)
        except Exception:
            pass

    # 最多重试 3 次
    for attempt in range(3):
        try:
            shutil.rmtree(path, onerror=_remove_readonly)
            if not path.exists():
                return
        except Exception:
            pass
        time.sleep(0.2)
    # 如果仍然失败，尝试逐文件清理
    try:
        for item in list(path.iterdir()):
            try:
                if item.is_dir():
                    _safe_rmtree(item)
                else:
                    try:
                        os.chmod(item, stat.S_IWRITE | stat.S_IREAD)
                        item.unlink()
                    except Exception:
                        pass
            except Exception:
                pass
        path.rmdir()
    except Exception:
        pass


def uninstall_package(path: Path):
    plugin_name = path.name
    requirements_file = path / "requirements.txt"
    if requirements_file.exists():
        try:
            log.debug(f"开始处理插件 {plugin_name} 的依赖")
            with open(requirements_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
            pm = PackageManager()
            uninstall_packages = []
            for line in lines:
                parsed = parse_requirement_line(line)
                if parsed:
                    package_name, _ = parsed
                    if package_name in package_requirements:
                        package_requirements[package_name].remove(plugin_name)
                        if not package_requirements[package_name]:
                            del package_requirements[package_name]
                            uninstall_packages.append(package_name)
                    else:
                        log.warning(f"插件 {package_name} 没有找到依赖关系")
            if uninstall_packages:
                log.debug(f"卸载 {plugin_name} 的依赖 {uninstall_packages}")
                pm.uninstall(uninstall_packages)
        except Exception as e:
            log.error(f"处理插件 {plugin_name} 的依赖时出错：{e}")


class PluginRuntimeManager:
    """插件运行时管理器，负责热重载已加载的插件模块。"""

    @staticmethod
    def _clear_nonebot_plugin_registry(module_name: str):
        """清理 NoneBot 中已经残留的插件注册信息，避免重复加载。"""
        nb_plugin = getattr(nonebot, "plugin", None)
        if nb_plugin is None:
            return
        plugin_module_name = module_name.rsplit(".", 1)[-1]
        stale_plugin_ids = []
        for plugin_id, plugin in list(getattr(nb_plugin, "_plugins", {}).items()):
            if (
                plugin.module_name == module_name
                or plugin.module_name.startswith(f"{module_name}.")
                or plugin.name == plugin_module_name
                or plugin.id_ == plugin_module_name
                or plugin.id_.startswith(f"{plugin_module_name}:")
            ):
                stale_plugin_ids.append(plugin_id)
        for plugin_id in stale_plugin_ids:
            plugin = nb_plugin._plugins.get(plugin_id)
            if plugin is not None:
                nb_plugin._revert_plugin(plugin)
        for manager in list(getattr(nb_plugin, "_managers", [])):
            removed_ids = []
            for plugin_id, controlled_module in list(
                manager.controlled_modules.items()
            ):
                if controlled_module == module_name or controlled_module.startswith(
                    f"{module_name}."
                ):
                    removed_ids.append(plugin_id)
            for plugin_id in removed_ids:
                if plugin_id in getattr(manager, "_third_party_plugin_ids", {}):
                    del manager._third_party_plugin_ids[plugin_id]
                if plugin_id in getattr(manager, "_searched_plugin_ids", {}):
                    del manager._searched_plugin_ids[plugin_id]
            if not manager.controlled_modules:
                try:
                    nb_plugin._managers.remove(manager)
                except ValueError:
                    pass

    @staticmethod
    def resolve_plugin_name(plugin_name: str) -> str:
        s = ServiceTools.get_service(plugin_name)
        if s is None:
            raise PluginError(f"没有发现插件: {plugin_name}")
        return s.module_name

    @staticmethod
    async def unload_plugin(plugin_name: str):
        module_name = PluginRuntimeManager.resolve_plugin_name(plugin_name)
        s = ServiceTools.get_service(plugin_name)
        if s:
            await s.unload()
        PluginRuntimeManager._clear_nonebot_plugin_registry(module_name)
        for key in list(sys.modules):
            if key == module_name or key.startswith(f"{module_name}."):
                del sys.modules[key]
        ServiceTools.service_list.pop(plugin_name, None)
        log.info(f"已卸载插件模块：{module_name}")

    @staticmethod
    async def reload_plugin(plugin_name: str):
        module_name = PluginRuntimeManager.resolve_plugin_name(plugin_name)
        await PluginRuntimeManager.unload_plugin(plugin_name)
        nonebot.load_plugin(module_name)
        module = importlib.import_module(module_name)
        log.success(f"已热重载插件：{module_name}")
        return module


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
        if plugin_name not in cls.plugin_list:
            log.info(f"安装未知插件`{plugin_name}`")
            raise PluginError(f"找不到插件 {plugin_name}")
        _plugin = cls.plugin_list[plugin_name]
        if repo := _plugin.get("repo", None):
            path = await cls.install_github_plugin(repo)
            if load:
                if not path or not str(path).strip():
                    raise PluginError(
                        f"安装插件 {plugin_name} 失败：未返回有效插件路径 {path}"
                    )
                path = path.replace("\\", ".")
                nonebot.load_plugin(path)
            return
        res_list = _plugin.get("res")
        try:
            if res_list:
                log.info(f"开始为插件`{plugin_name}`下载资源")
                for res in res_list:
                    data = await request.get(FILE_URL.format(res))
                    if data.status_code != 200:
                        log.warning(
                            f"在下载插件`{plugin_name}`的资源时网络连接错误，code:{data.status_code}"
                        )
                        raise PluginError(f"网络连接错误，code:{data.status_code}")
                    if "." in res:
                        file = data.json()
                        file_list = [file]
                    else:
                        file_list = data.json()
                    await cls.download_github_file(file_list)
        except Exception as e:
            log.warning(f"插件`{plugin_name}`资源安装失败:发生错误{e}")
            raise PluginError(f"插件资源安装失败:{e}")
        path: str = _plugin["path"]
        p_path = path.replace(".", "/")
        try:
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
                await cls.download_github_file(file_list)
            except PluginError:
                # 清理已下载的文件或目录
                try:
                    target_path = Path(".") / p_path
                    if target_path.exists():
                        log.info(f"清理插件目录 {target_path}...")
                        if _plugin["is_dir"]:
                            _safe_rmtree(target_path)
                        else:
                            target_path.unlink()
                        log.info(f"已清理插件 {plugin_name} 的残留文件")
                except Exception as cleanup_error:
                    log.error(f"清理插件 {plugin_name} 残留文件失败: {cleanup_error}")
                raise
            req_path = Path(".") / p_path / "requirements.txt"
            if req_path.exists():
                log.info(f"开始为`{plugin_name}`安装依赖")
                stdout, _ = await _run_shell_command(
                    _build_shell_command(["pip", "install", "-r", str(req_path)])
                )
                log.debug(f"`{plugin_name}`依赖安装信息: stdout={stdout}")
            if load:
                nonebot.load_plugin(path)
            log.info(f"插件`{plugin_name}`安装结束")
        except Exception as e:
            log.warning(f"插件`{plugin_name}`安装失败:发生错误{e}")
            raise PluginError(f"插件安装失败:{e}")

    @classmethod
    async def remove_plugin(cls, plugin_name: str):
        if plugin_name not in ServiceTools.service_list:
            raise PluginError(f"未安装插件 {plugin_name}")
        if plugin_name not in cls.plugin_list:
            raise PluginError(f"无法卸载 {plugin_name}，请手动卸载")
        _plugin = cls.plugin_list[plugin_name]
        if repo := _plugin.get("repo", None):
            await cls.remove_github_plugin(repo)
            await PluginRuntimeManager.unload_plugin(plugin_name)
            return
        path = cls.plugin_list[plugin_name]["path"].replace(".", "/")
        if not cls.plugin_list[plugin_name]["is_dir"]:
            path += ".py"
            os.remove(path)
        else:
            uninstall_package(path)
            shutil.rmtree(path)
        await PluginRuntimeManager.unload_plugin(plugin_name)
        log.info(f"插件`{plugin_name}`移除成功")

    @classmethod
    async def install_github_plugin(cls, repo: str) -> str:
        log.info(f"开始从 GitHub 克隆插件：{repo}")
        # 从 repo URL 中提取项目名称
        if repo.endswith(".git"):
            repo_name = repo.split("/")[-1].replace(".git", "")
        else:
            repo_name = repo.split("/")[-1]
        target_path = PLUGINS_DIR / repo_name
        try:
            # 如果目录已存在，先删除
            if target_path.exists():
                log.warning(f"目录 {target_path} 已存在，正在删除...")
                _safe_rmtree(target_path)
            # 执行 git clone
            stdout, _ = await _run_shell_command(
                _build_shell_command(["git", "clone", repo, str(target_path)])
            )
            log.debug(f"Git clone 输出：{stdout}")
            log.info(f"成功克隆插件：{repo_name} 到 {target_path}")
            # 检查并安装依赖
            req_path = target_path / "requirements.txt"
            if req_path.exists():
                log.info(f"开始为 `{repo_name}` 安装依赖")
                stdout, _ = await _run_shell_command(
                    _build_shell_command(["pip", "install", "-r", str(req_path)])
                )
                log.debug(f"`{repo_name}` 依赖安装信息：{stdout}")
                log.info("依赖安装完成")
            log.info(f"GitHub 插件 `{repo_name}` 安装结束")
            return str(target_path)
        except subprocess.CalledProcessError as e:
            log.error(f"Git clone 失败：{e.stderr}")
            if target_path.exists():
                log.info(f"清理插件目录 {target_path}...")
                _safe_rmtree(target_path)
            raise PluginError(f"Git clone 失败：{e.stderr}")
        except Exception as e:
            log.error(f"安装 GitHub 插件失败：{e}")
            if target_path.exists():
                log.info(f"清理插件目录 {target_path}...")
                _safe_rmtree(target_path)
            raise PluginError(f"安装 GitHub 插件失败：{e}")

    @classmethod
    async def remove_github_plugin(cls, repo: str):
        log.info(f"开始卸载 GitHub插件：{repo}")
        try:
            # 从 repo URL 中提取项目名称
            if repo.endswith(".git"):
                repo_name = repo.split("/")[-1].replace(".git", "")
            else:
                repo_name = repo.split("/")[-1]
            target_path = PLUGINS_DIR / repo_name
            # 检查目录是否存在
            if not target_path.exists():
                raise PluginError(f"插件目录 {target_path} 不存在")
            uninstall_package(target_path)
            # 删除插件目录（使用安全删除）
            _safe_rmtree(target_path)
            log.info(f"成功卸载 GitHub插件：{repo_name}")
        except Exception as e:
            log.error(f"卸载 GitHub插件失败：{e}")
            raise PluginError(f"卸载 GitHub插件失败：{e}")

    @classmethod
    async def update_github_plugin(cls, repo: str):
        log.info(f"开始更新 GitHub 插件：{repo}")
        try:
            # 从 repo URL 中提取项目名称
            if repo.endswith(".git"):
                repo_name = repo.split("/")[-1].replace(".git", "")
            else:
                repo_name = repo.split("/")[-1]
            target_path = PLUGINS_DIR / repo_name
            # 检查目录是否存在
            if not target_path.exists():
                raise PluginError(f"插件目录 {target_path} 不存在，请先安装插件")
            # 检查是否是 git 仓库
            git_dir = target_path / ".git"
            if not git_dir.exists():
                raise PluginError(f"{target_path} 不是 git 仓库，无法更新")
            # 执行 git pull
            stdout, _ = await _run_shell_command(
                _build_shell_command(["git", "pull", "--force"]),
                cwd=target_path,
            )
            log.debug(f"Git pull 输出：{stdout}")
            # 检查是否有更新
            if "Already up to date" in stdout or "已经是最新的" in stdout:
                log.info(f"GitHub 插件 `{repo_name}` 已经是最新版本")
            else:
                log.info(f"成功更新 GitHub 插件：{repo_name}")
                # 检查并安装依赖
                req_path = target_path / "requirements.txt"
                if req_path.exists():
                    log.info(f"开始为 `{repo_name}` 安装依赖")
                    stdout, _ = await _run_shell_command(
                        _build_shell_command(["pip", "install", "-r", str(req_path)])
                    )
                    log.debug(f"`{repo_name}` 依赖安装信息：{stdout}")
                    log.info("依赖安装完成")
            log.info(f"GitHub 插件 `{repo_name}` 更新结束")
        except subprocess.CalledProcessError as e:
            log.error(f"Git pull 失败：{e.stderr}")
            raise PluginError(f"Git pull 失败：{e.stderr}")
        except Exception as e:
            log.error(f"更新 GitHub 插件失败：{e}")
            raise PluginError(f"更新 GitHub 插件失败：{e}")

    @classmethod
    async def check_github_plugin_update(cls, repo: str):
        """
        检查 GitHub 插件是否有更新
        :param repo: GitHub 仓库地址
        :return: dict 包含更新信息 {'has_update': bool, 'local_version': str, 'remote_version': str}
        """
        log.debug(f"开始检查 GitHub 插件更新：{repo}")
        try:
            # 从 repo URL 中提取项目名称
            if repo.endswith(".git"):
                repo_name = repo.split("/")[-1].replace(".git", "")
            else:
                repo_name = repo.split("/")[-1]
            target_path = PLUGINS_DIR / repo_name
            # 检查目录是否存在
            if not target_path.exists():
                raise PluginError(f"插件目录 {target_path} 不存在")
            # 读取本地 meta.yml
            local_meta_path = target_path / "meta.yml"
            if not local_meta_path.exists():
                raise PluginError("未找到本地 meta.yml 文件")
            async with await anyio.open_file(
                local_meta_path, "r", encoding="utf-8"
            ) as f:
                local_meta = yaml.safe_load(await f.read())
            local_version = local_meta.get("version", "unknown")
            # 获取远程 meta.yml
            remote_meta = await cls.get_github_plugin_meta(repo)
            remote_version = remote_meta.get("version", "unknown")
            # 比较版本号
            has_update = local_version != remote_version
            result = {
                "has_update": has_update,
                "local_version": local_version,
                "remote_version": remote_version,
                "repo_name": repo_name,
            }
            if has_update:
                log.debug(
                    f"发现新版本：{repo_name} {local_version} -> {remote_version}"
                )
            return result
        except yaml.YAMLError as e:
            log.error(f"解析 meta.yml 失败：{e}")
            raise PluginError(f"解析 meta.yml 失败：{e}")
        except Exception as e:
            log.error(f"检查 GitHub 插件更新失败：{e}")
            raise PluginError(f"检查 GitHub 插件更新失败：{e}")

    @staticmethod
    async def get_github_plugin_meta(repo: str):
        """
        获取 GitHub 插件的 meta.yml 信息
        :param repo: GitHub 仓库地址
        :return: dict 包含 meta 信息
        """
        if repo.startswith("https://github.com/"):
            # 从 https://github.com/user/repo 转换为 https://raw.githubusercontent.com/user/repo/main/meta.yml
            repo_path = repo.replace("https://github.com/", "").rstrip("/")
            remote_meta_url = (
                f"https://raw.githubusercontent.com/{repo_path}/main/meta.yml"
            )
        else:
            raise PluginError(f"不支持的仓库类型：{repo}")
        response = await request.get(remote_meta_url, follow_redirects=True)
        if response.status_code != 200:
            raise PluginError(f"无法获取远程 meta.yml，状态码：{response.status_code}")
        return yaml.safe_load(response.text)

    @staticmethod
    async def download_github_file(file_list):
        """
        下载 GitHub 文件
        :param file_list: 下载保存路径列表
        :return: None
        """
        for file in file_list:
            file_path = Path(".") / file["path"]
            temporary_path = file_path.with_name(f".{file_path.name}.part")
            file_path.parent.mkdir(parents=True, exist_ok=True)
            last_error = None
            for attempt in range(1, DOWNLOAD_RETRIES + 1):
                try:
                    data = await request.get(file["download_url"])
                    if data.status_code != 200:
                        raise PluginError(
                            f"下载文件失败: {file_path}, 状态码: {data.status_code}"
                        )
                    content = data.content
                    if not content:
                        raise PluginError(f"下载的文件为空: {file_path}")
                    expected_sha = file.get("sha")
                    if expected_sha:
                        blob = f"blob {len(content)}\0".encode() + content
                        actual_sha = hashlib.sha1(blob).hexdigest()
                        if actual_sha != expected_sha:
                            raise PluginError(
                                f"下载文件校验失败: {file_path}, "
                                f"期望 {expected_sha}, 实际 {actual_sha}"
                            )
                    log.debug(f"下载文件`{file_path}`")
                    async with await anyio.open_file(temporary_path, "wb") as f:
                        await f.write(content)
                    os.replace(temporary_path, file_path)
                    break
                except (httpx.HTTPError, OSError, PluginError) as e:
                    last_error = e
                    temporary_path.unlink(missing_ok=True)
                    if attempt < DOWNLOAD_RETRIES:
                        log.warning(
                            f"下载文件`{file_path}`失败，第 {attempt} 次重试: {e}"
                        )
                        await asyncio.sleep(DOWNLOAD_RETRY_DELAY)
                    else:
                        raise PluginError(
                            f"下载文件失败（重试 {DOWNLOAD_RETRIES} 次）: "
                            f"{file_path}: {last_error}"
                        ) from e

    @classmethod
    async def check_update(cls, plugin_name: str):
        if plugin_name not in cls.plugin_list:
            return None
        _plugin = cls.plugin_list[plugin_name]
        version = _plugin.get("version", "null")
        if plugin_name in ServiceTools.service_list:
            if version == "github":
                r = await cls.check_github_plugin_update(_plugin["repo"])
                if r["has_update"]:
                    return r["local_version"], r["remote_version"]
                return None
            now_version = ServiceTools(plugin_name).load_service().version
            if now_version != version:
                return now_version, version
        return None

    @classmethod
    async def update_plugin(cls, plugin_name: str):
        if plugin_name not in cls.plugin_list:
            return False, f"找不到插件 {plugin_name}"
        version = cls.plugin_list[plugin_name]["version"]
        from ATRI.service import ServiceTools

        if (
            plugin_name in ServiceTools.service_list
            and await cls.check_update(plugin_name) is None
        ):
            return False, f"{plugin_name} 无需更新"
        try:
            _plugin = cls.plugin_list[plugin_name]
            if repo := _plugin.get("repo", None):
                await cls.update_github_plugin(repo)
            else:
                await cls.install_plugin(plugin_name)
            try:
                await PluginRuntimeManager.reload_plugin(plugin_name)
                return True, f"{plugin_name}-{version} 更新成功，已热重载插件"
            except Exception as e:
                log.warning(f"插件 {plugin_name} 热重载失败：{e}")
                return (
                    True,
                    f"{plugin_name}-{version}安装成功，请重启以启用新版插件",
                )
        except PluginError as e:
            return False, f"更新插件失败：{e.prompt}"
        except Exception:
            raise
