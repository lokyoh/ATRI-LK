import asyncio
import contextlib
import subprocess
import sys
from pathlib import Path

from ATRI import load, service
from ATRI.log import log
from ATRI.utils.package_manager import PackageManager

from . import entries, state


def pip_install_args(req_path: Path) -> list[str]:
    """依赖安装的命令行参数，固定装进当前解释器。

    直接调 PATH 上的 ``pip`` 可能落到另一个解释器（venv / poetry / Docker
    里 PATH 未必指向正在运行的这个），装完当前进程仍然 import 不到。
    返回参数列表而非拼接后的字符串：调用方用 exec 直传，不经 shell。
    """
    return [sys.executable, "-m", "pip", "install", "-r", str(req_path)]


async def run_command(
    args: list[str], cwd: str | Path | None = None, timeout: float | None = None
):
    """执行命令，参数以列表直传（不经 shell）。

    走 shell 会让插件目录名里的 ``&`` ``|`` ``;`` 被解释——这些名字来自
    外部仓库，属于不可控输入。

    ``timeout`` 到点直接杀掉：pip 卡在索引/编译上时调用方还握着全局安装锁，
    不设上限会让之后所有插件的安装/更新永久排队。
    """
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd) if cwd is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    limit = state.DEP_TIMEOUT if timeout is None else float(timeout)
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(), timeout=limit
        )
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        with contextlib.suppress(Exception):
            await process.wait()
        raise subprocess.TimeoutExpired(args, limit) from None
    stdout = stdout_bytes.decode(errors="replace")
    stderr = stderr_bytes.decode(errors="replace")
    if process.returncode != 0:
        raise subprocess.CalledProcessError(
            process.returncode,
            args,
            output=stdout,
            stderr=stderr,
        )
    return stdout, stderr


def register_requirements(path: str | Path):
    """把插件 requirements.txt 里的包登记到依赖账本。

    ``load.py`` 只在启动扫描 ``plugins/`` 时记账，运行时装出来的插件不在账上，
    卸载时 ``uninstall_package`` 找不到依赖关系就不会卸包，pip 包永久留下。
    """
    path = entries.to_plugin_path(path)
    requirements_file = path / "requirements.txt"
    if not requirements_file.is_file():
        return
    try:
        lines = requirements_file.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        log.warning(f"读取 {requirements_file} 失败，依赖未登记：{e}")
        return
    for line in lines:
        parsed = load.parse_requirement_line(line)
        if not parsed:
            continue
        package_name, _ = parsed
        owners = load.package_requirements.setdefault(package_name, [])
        if path.name not in owners:
            owners.append(path.name)


def uninstall_package(path: str | Path):
    path = entries.to_plugin_path(path)
    plugin_name = path.name
    requirements_file = path / "requirements.txt"
    if requirements_file.exists():
        try:
            log.debug(f"开始处理插件 {plugin_name} 的依赖")
            with open(requirements_file, encoding="utf-8") as f:
                lines = f.readlines()
            pm = PackageManager()
            uninstall_packages = []
            for line in lines:
                parsed = load.parse_requirement_line(line)
                if parsed:
                    package_name, _ = parsed
                    pkg_reqs = load.package_requirements
                    if package_name in pkg_reqs:
                        pkg_reqs[package_name].remove(plugin_name)
                        if not pkg_reqs[package_name]:
                            del pkg_reqs[package_name]
                            uninstall_packages.append(package_name)
                    else:
                        log.warning(f"插件 {package_name} 没有找到依赖关系")
            if uninstall_packages:
                log.debug(f"卸载 {plugin_name} 的依赖 {uninstall_packages}")
                pm.uninstall(uninstall_packages)
        except Exception as e:
            log.error(f"处理插件 {plugin_name} 的依赖时出错：{e}")


def installed_root(plugin_name: str) -> Path | None:
    """已装插件的本地落点，按模块名推断；推断不出返回 None。"""
    svc = service.ServiceTools.get_service(plugin_name)
    module_name = getattr(svc, "module_name", "") or ""
    if not module_name:
        return None
    base = Path(*module_name.split("."))
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    if base.is_dir():
        return base
    return None
