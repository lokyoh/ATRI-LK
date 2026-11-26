from pathlib import Path

import nonebot
from nonebot.plugin.load import path_to_module_name

from ATRI import service
from ATRI.exceptions import PluginError
from ATRI.log import log
from ATRI.utils import git

from . import deps, paths, progress, state, transaction, verify
from .entries import PluginEntry, repo_name, to_plugin_path
from .runtime import PluginRuntimeManager


async def install_github_plugin(cls, repo: str) -> str:
    log.info(f"开始从 GitHub 克隆插件：{repo}")
    name = repo_name(repo)
    target_path = state.PLUGINS_DIR / name
    try:
        # 如果目录已存在，先删除；删不掉就别往下走，否则 clone 撞「目录非空」
        if target_path.exists():
            log.warning(f"目录 {target_path} 已存在，正在删除...")
            if not paths.safe_rmtree(target_path):
                raise PluginError(f"删除旧目录 {target_path} 失败，请关闭占用后重试")
        cloned = await git.clone(repo, target_path)
        if not cloned.ok:
            raise PluginError(f"Git clone 失败：{cloned.detail}")
        log.debug(f"Git clone 输出：{cloned.stdout}")
        log.info(f"成功克隆插件：{name} 到 {target_path}")
        # 检查并安装依赖
        req_path = target_path / "requirements.txt"
        if req_path.exists():
            log.info(f"开始为 `{name}` 安装依赖")
            stdout, _ = await deps.run_command(deps.pip_install_args(req_path))
            log.debug(f"`{name}` 依赖安装信息：{stdout}")
            deps.register_requirements(target_path)
            log.info("依赖安装完成")
        log.info(f"GitHub 插件 `{name}` 安装结束")
        return str(target_path)
    except PluginError as e:
        log.error(f"Git clone 失败：{e.prompt}")
        if target_path.exists():
            log.info(f"清理插件目录 {target_path}...")
            paths.safe_rmtree(target_path)
        raise
    except Exception as e:
        log.error(f"安装 GitHub 插件失败：{e}")
        if target_path.exists():
            log.info(f"清理插件目录 {target_path}...")
            paths.safe_rmtree(target_path)
        raise PluginError(f"安装 GitHub 插件失败：{e}")


async def remove_github_plugin(cls, repo: str):
    log.info(f"开始卸载 GitHub插件：{repo}")
    try:
        name = repo_name(repo)
        target_path = state.PLUGINS_DIR / name
        # 检查目录是否存在
        if not target_path.exists():
            raise PluginError(f"插件目录 {target_path} 不存在")
        deps.uninstall_package(target_path)
        # 删除插件目录（使用安全删除）
        if not paths.safe_rmtree(target_path):
            raise PluginError(f"插件目录 {target_path} 未能完全删除，请手动清理")
        log.info(f"成功卸载 GitHub插件：{name}")
    except PluginError:
        raise
    except Exception as e:
        log.error(f"卸载 GitHub插件失败：{e}")
        raise PluginError(f"卸载 GitHub插件失败：{e}")


async def update_github_plugin(cls, repo: str):
    log.info(f"开始更新 GitHub 插件：{repo}")
    name = repo_name(repo)
    target_path = state.PLUGINS_DIR / name
    previous_commit = ""
    try:
        if not target_path.exists():
            raise PluginError(f"插件目录 {target_path} 不存在，请先安装插件")
        if not (target_path / ".git").exists():
            raise PluginError(f"{target_path} 不是 git 仓库，无法更新")
        previous_commit = await git.head_commit(target_path)
        pulled = await git.pull(target_path)
        if not pulled.ok:
            raise PluginError(f"Git pull 失败：{pulled.detail}")
        log.debug(f"Git pull 输出：{pulled.stdout}")
        stdout = pulled.stdout
        if "Already up to date" in stdout or "已经是最新的" in stdout:
            log.info(f"GitHub 插件 `{name}` 已经是最新版本")
        else:
            log.info(f"成功更新 GitHub 插件：{name}")
            req_path = target_path / "requirements.txt"
            if req_path.exists():
                log.info(f"开始为 `{name}` 安装依赖")
                stdout, _ = await deps.run_command(deps.pip_install_args(req_path))
                log.debug(f"`{name}` 依赖安装信息：{stdout}")
                deps.register_requirements(target_path)
                log.info("依赖安装完成")
        log.info(f"GitHub 插件 `{name}` 更新结束")
    except Exception as e:
        detail = getattr(e, "stderr", "") or str(e)
        log.error(f"更新 GitHub 插件失败：{detail}")
        await cls._rollback_github_plugin(target_path, previous_commit)
        raise PluginError(f"更新 GitHub 插件失败：{detail}") from e


async def rollback_github_plugin(cls, target_path: Path, commit: str):
    """把插件目录退回到更新前的 commit，并撤销 pull 的改动。

    回滚同样可能失败（例如工作区被外部改动），失败只记日志：此时抛出
    的仍是「更新失败」，不该被回滚错误盖掉。
    """
    await git.rollback_to(commit, cwd=target_path)


async def install_entry(cls, plugin_name: str, entry: PluginEntry, load: bool):
    """按条目类型装一个插件（不含备份/回滚，由调用方负责）。"""
    if entry.repo:
        path = await cls.install_github_plugin(entry.repo)
        if load:
            if not path or not str(path).strip():
                raise PluginError(
                    f"安装插件 {plugin_name} 失败：未返回有效插件路径 {path}"
                )
            nonebot.load_plugin(path_to_module_name(Path(path)))
        return
    res_list = entry.res
    target = entry.target_path
    allowed_root = target if entry.is_dir else target.parent
    try:
        if res_list:
            log.info(f"开始为插件`{plugin_name}`下载资源")
            progress.update(plugin_name, "resource", f"共 {len(res_list)} 个资源")
            for res in res_list:
                file_list = await cls._collect_download_files(res, is_dir=True)
                await cls.download_github_file(file_list, paths.RES_DIR)
    except Exception as e:
        log.warning(f"插件`{plugin_name}`资源安装失败:发生错误{e}")
        raise PluginError(f"插件资源安装失败:{e}")
    path: str = entry.path
    p_path = path.replace(".", "/")
    try:
        log.debug(f"开始下载插件`{plugin_name}`")
        progress.update(plugin_name, "fetch", path)
        target_rel = p_path if entry.is_dir else f"{p_path}.py"
        file_list = await cls._collect_download_files(target_rel, entry.is_dir)
        await cls.download_github_file(file_list, allowed_root)
        progress.update(plugin_name, "validate")
        problems = verify.validate_plugin_files(target, entry.is_dir)
        if problems:
            for problem in problems:
                log.warning(f"插件`{plugin_name}`校验未通过：{problem}")
            raise PluginError(f"插件校验未通过：{'；'.join(problems)}")
        req_path = target / "requirements.txt"
        if entry.is_dir and req_path.exists():
            log.info(f"开始为`{plugin_name}`安装依赖")
            progress.update(plugin_name, "deps")
            stdout, _ = await deps.run_command(deps.pip_install_args(req_path))
            log.debug(f"`{plugin_name}`依赖安装信息: stdout={stdout}")
            deps.register_requirements(target)
        if load:
            progress.update(plugin_name, "load")
            nonebot.load_plugin(path)
        log.info(f"插件`{plugin_name}`安装结束")
    except Exception as e:
        log.warning(f"插件`{plugin_name}`安装失败:发生错误{e}")
        raise PluginError(f"插件安装失败:{e}")


async def install_plugin(cls, plugin_name: str, load: bool = False):
    if plugin_name not in cls.plugin_list:
        log.info(f"安装未知插件`{plugin_name}`")
        raise PluginError(f"找不到插件 {plugin_name}")
    entry = await cls.get_entry(plugin_name)
    compat = cls.compat(entry)
    if not compat.ok:
        log.warning(f"插件`{plugin_name}`与当前环境不匹配：{compat.reason}")
    progress.start(plugin_name, "install")
    target: Path | None = None
    backup: Path | None = None
    existed = False
    try:
        async with state.install_lock_for(plugin_name), state.global_install_gate():
            # 备份必须在锁内取：锁外备份可能是另一个协程正在改写的半成品
            try:
                target = entry.target_path
                existed = target.exists()
                if existed:
                    backup = transaction.backup_plugin_dir(target)
                    if backup is None:
                        raise PluginError(
                            f"备份 {target} 失败，已中止安装（旧版本保持原样）"
                        )
            except PluginError:
                target = None
            await cls._install_plugin(plugin_name, entry, load)
    except Exception as e:
        transaction.rollback_install(backup, target, existed)
        transaction.drop_backup(backup)
        progress.finish(plugin_name, ok=False, message=str(e))
        raise
    else:
        transaction.drop_backup(backup)
        state.forget_update_state(plugin_name)
    finally:
        state.release_install_lock(plugin_name)
    progress.finish(plugin_name, ok=True, message="安装完成")


async def remove_plugin(cls, plugin_name: str):
    if plugin_name not in service.ServiceTools.service_list:
        raise PluginError(f"未安装插件 {plugin_name}")
    # 索引只补元信息；插件下架或索引拉取失败时仍要能卸，按本地落点反推
    raw = cls.plugin_list.get(plugin_name) or {}
    entry = PluginEntry.from_mapping(plugin_name, raw)
    if not entry.repo and not entry.path:
        local_root = cls._installed_root(plugin_name)
        if local_root is None:
            raise PluginError(f"无法定位 {plugin_name} 的安装位置，请手动卸载")
        entry = PluginEntry.from_mapping(
            plugin_name, {"path": str(local_root), "is_dir": local_root.is_dir()}
        )
    progress.start(plugin_name, "remove", "cleanup")
    try:
        async with state.install_lock_for(plugin_name), state.global_install_gate():
            if entry.repo:
                await cls.remove_github_plugin(entry.repo)
            else:
                raw_path = entry.path
                if not raw_path:
                    raise PluginError(f"插件 {plugin_name} 未登记路径信息，请手动卸载")
                path = to_plugin_path(raw_path)
                if not entry.is_dir:
                    if path.suffix != ".py":
                        path = path.with_name(f"{path.name}.py")
                    if path.exists():
                        path.unlink()
                    else:
                        log.warning(f"插件文件 {path} 不存在，跳过删除")
                else:
                    deps.uninstall_package(path)
                    paths.safe_rmtree(path)
                    if path.exists():
                        log.warning(f"插件目录 {path} 未能完全删除，请手动清理")
            await PluginRuntimeManager.unload_plugin(plugin_name)
            transaction.remove_service_files(plugin_name)
    except PluginError as e:
        progress.finish(plugin_name, ok=False, message=e.prompt)
        raise
    except Exception as e:
        progress.finish(plugin_name, ok=False, message=str(e))
        raise
    finally:
        state.release_install_lock(plugin_name)
    progress.finish(plugin_name, ok=True, message="移除完成")
    state.forget_update_state(plugin_name)
    log.info(f"插件`{plugin_name}`移除成功")


async def update_plugin(cls, plugin_name: str, force: bool = False):
    """更新一个插件。

    ``force=True`` 时跳过「是否已有新版」的判断直接重装：用于版本号没变
    但内容需要修复、或上次更新不完整的情况。WebUI 的「强制更新」走这里。
    """
    if plugin_name not in cls.plugin_list:
        return False, f"找不到插件 {plugin_name}"
    entry = await cls.get_entry(plugin_name)
    version = entry.version
    compat = cls.compat(entry)
    if not compat.ok:
        log.warning(f"插件`{plugin_name}`与当前环境不匹配：{compat.reason}")

    if (
        not force
        and plugin_name in service.ServiceTools.service_list
        and await cls.check_update(plugin_name) is None
    ):
        return False, f"{plugin_name} 已是最新版本，无需更新（可用强制更新重装）"
    progress.start(plugin_name, "update")
    backup: Path | None = None
    target: Path | None = None
    try:
        async with state.install_lock_for(plugin_name), state.global_install_gate():
            if not entry.repo:
                try:
                    target = entry.target_path
                except PluginError:
                    target = None
                if target is not None and target.exists():
                    backup = transaction.backup_plugin_dir(target)
                    if backup is None:
                        raise PluginError(f"备份 {target} 失败，已中止更新")
            if entry.repo:
                progress.update(plugin_name, "fetch")
                await cls.update_github_plugin(entry.repo)
            else:
                await cls._install_plugin(plugin_name, entry, False)
    except PluginError as e:
        transaction.restore_plugin_dir(backup, target)
        transaction.drop_backup(backup)
        progress.finish(plugin_name, ok=False, message=e.prompt)
        return False, f"更新插件失败：{e.prompt}"
    except Exception:
        transaction.restore_plugin_dir(backup, target)
        transaction.drop_backup(backup)
        raise
    else:
        transaction.drop_backup(backup)
        state.forget_update_state(plugin_name)
    finally:
        state.release_install_lock(plugin_name)

    hint = f"（注意：{compat.reason}）" if not compat.ok else ""
    try:
        progress.update(plugin_name, "reload")
        await PluginRuntimeManager.reload_plugin(plugin_name)
        progress.finish(plugin_name, ok=True, message="更新完成，已热重载")
        return True, f"{plugin_name}-{version} 更新成功，已热重载插件{hint}"
    except Exception as e:
        log.warning(f"插件 {plugin_name} 热重载失败：{e}")
        progress.finish(plugin_name, ok=True, message="更新完成，需重启生效")
        return (
            True,
            f"{plugin_name}-{version}安装成功，请重启以启用新版插件{hint}",
        )
