import asyncio
import shutil
from dataclasses import dataclass

from ATRI import __sub_version__, __version__
from ATRI.log import log
from ATRI.message import MessageBuilder
from ATRI.utils import git
from ATRI.utils.check_update import CheckUpdate, is_newer_version

from .config import BETA_BRANCH, OFFICIAL_BRANCH
from .deployment import code_mounted, entrypoint_path, in_container, update_hint

SERVICE_NAME = "更新"
"""插件服务名，用于取插件配置。"""

REPO_DIR = "."
"""系统仓库所在目录（进程 cwd 即仓库根）。"""

DEPENDENCY_FILES = ("pyproject.toml", "poetry.lock")
"""依赖清单。这两个文件变了才需要重装依赖。"""

POETRY_INSTALL_ARGS = (
    "install",
    "--no-interaction",
    "--no-ansi",
    "--no-root",
    "--only",
    "main",
)
"""与 Dockerfile 保持一致的安装参数：只装主依赖，不装项目自身。"""


def config():
    """当前更新插件配置；配置对象由 ``__init__`` 注册。"""
    from ATRI.configs import PluginConfig

    setting = PluginConfig.get_config(SERVICE_NAME)
    if setting is None:
        raise RuntimeError("更新插件配置未初始化")
    return setting


def current_branch_setting() -> str:
    """配置里选定的更新通道所对应的分支。"""
    return config().branch


@dataclass
class DependencyResult:
    ok: bool
    changed: bool
    message: str = ""


async def _stash_local_changes() -> tuple[bool, str]:
    """更新前备份本地改动，返回 ``(是否创建, stash 标记)``。"""
    mark = f"atri-auto-{_timestamp()}"
    created, _ = await git.stash_local_changes(REPO_DIR, message=mark)
    if created:
        log.warning(f"更新前已备份本地改动到 stash：{mark}")
        return True, mark
    return False, ""


def _timestamp() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y%m%d-%H%M%S")


async def _dependency_files_changed(previous: str, current: str) -> bool:
    """判断两个 commit 之间依赖清单是否有变化。

    取不到任一 commit 时保守地认为「变了」，宁可多装一次也不要漏装。
    """
    if not previous or not current:
        return True
    result = await git.run_git(
        "diff", "--name-only", previous, current, "--", *DEPENDENCY_FILES
    )
    if not result.ok:
        log.warning(f"对比依赖文件失败：{result.detail}")
        return True
    return bool(result.stdout.strip())


def _lock_out_of_sync(stderr: str) -> bool:
    """识别「锁文件与 pyproject 不同步」这类可自动修复的失败。"""
    text = stderr.lower()
    return "poetry.lock" in text and (
        "changed significantly" in text or "lock file" in text
    )


async def _spawn_poetry_install():
    proc = await asyncio.create_subprocess_exec(
        "poetry",
        *POETRY_INSTALL_ARGS,
        cwd=REPO_DIR,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr_b = await proc.communicate()
    return proc, stderr_b.decode(errors="replace")


async def _run_poetry(*args: str) -> DependencyResult:
    proc = await asyncio.create_subprocess_exec(
        "poetry",
        *args,
        cwd=REPO_DIR,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr_b = await proc.communicate()
    return DependencyResult(
        ok=proc.returncode == 0,
        changed=True,
        message=stderr_b.decode(errors="replace").strip(),
    )


async def _ensure_dependencies(previous: str, current: str) -> DependencyResult:
    """依赖清单有变化时执行 ``poetry install``。"""
    if not await _dependency_files_changed(previous, current):
        log.info("依赖清单未变化，跳过依赖安装")
        return DependencyResult(ok=True, changed=False)

    if shutil.which("poetry") is None:
        log.warning("未找到 poetry，无法自动安装依赖")
        return DependencyResult(
            ok=False,
            changed=True,
            message="依赖清单已更新，但当前环境找不到 poetry，请手动执行 poetry install",
        )

    log.info("依赖清单已变化，开始安装依赖...")
    try:
        proc, detail = await _spawn_poetry_install()
    except Exception as e:
        log.error(f"执行 poetry install 失败：{e}")
        return DependencyResult(ok=False, changed=True, message=f"依赖安装失败：{e}")

    if proc.returncode != 0 and _lock_out_of_sync(detail):
        # 发布时若漏了 `poetry lock`，锁文件会与 pyproject 不一致，
        # install 直接报错。此时重新生成锁再装一次。
        log.warning("poetry.lock 与 pyproject.toml 不同步，尝试重新生成锁文件")
        relock = await _run_poetry("lock")
        if not relock.ok:
            return DependencyResult(
                ok=False,
                changed=True,
                message="依赖安装失败：锁文件与 pyproject.toml 不一致，"
                "且自动重新生成锁文件失败，请手动执行 poetry lock && poetry install",
            )
        proc, detail = await _spawn_poetry_install()

    if proc.returncode != 0:
        log.error(f"poetry install 失败：{detail.strip()}")
        tail = detail.strip().splitlines()[-3:] if detail.strip() else []
        return DependencyResult(
            ok=False,
            changed=True,
            message="依赖安装失败，请手动执行 poetry install"
            + (f"\n{' / '.join(tail)}" if tail else ""),
        )

    log.info("依赖安装完成")
    return DependencyResult(ok=True, changed=True)


class Updater:
    @classmethod
    async def check(cls):
        message = MessageBuilder().text(f"当前版本: {__version__} {__sub_version__}")
        latest_info = await CheckUpdate.get_latest_info()
        if latest_info:
            if is_newer_version(latest_info.version, __version__, __sub_version__):
                message.text("新版本已发布,请更新!!!")
            message.text(
                f"远程版本: {latest_info.version} 更新时间: {latest_info.update_time}"
            )
            message.text(f"更新信息: {latest_info.info}")
            if in_container():
                message.text("当前是容器部署：/更新 后发送 /重启 即可，依赖会自动对齐")
            return message
        else:
            return message.text("最新版本获取失败")

    @classmethod
    async def update(cls, branch: str | None = None) -> str:
        """更新到指定分支的最新提交（默认取配置里的通道）。

        容器部署也走这条路径：代码挂在宿主机仓库上，容器内 ``git reset`` 改的
        就是那份代码。区别只在**依赖不在这里装**——重启时由启动脚本按
        ``poetry.lock`` 对齐，避免在运行中的容器里临时装包。
        """
        if not await git.is_git_repo(REPO_DIR):
            return "当前不是 git 仓库，无法自动更新（请手动替换文件）"
        if in_container() and entrypoint_path() is None:
            # 没有启动脚本，重启时没人对齐依赖；硬更新只会留下半吊子状态。
            log.warning("容器部署但缺少 ATRI_ENTRYPOINT，无法在重启时对齐依赖")
            return update_hint()
        target = branch or current_branch_setting()
        return await cls._switch_to(target, target)

    @classmethod
    async def switch_channel(cls, channel: str) -> str:
        """切换更新通道（main / dev），并立即同步到该分支。"""
        branch = channel if channel in (OFFICIAL_BRANCH, BETA_BRANCH) else None
        if branch is None:
            return f"未知通道：{channel}（可用：{OFFICIAL_BRANCH} / {BETA_BRANCH}）"
        setting = config()
        setting.set("channel", branch)
        return await cls.update(branch)

    @classmethod
    async def _restore(cls, commit: str, branch: str) -> bool:
        """把仓库恢复到更新前的「分支 + commit」。

        只回滚 commit 不切分支的话，用户会莫名停在一个不是他原本所在的分支上。
        """
        if branch:
            back = await git.checkout_branch(branch, cwd=REPO_DIR)
            if not back.ok:
                log.error(f"切回分支 {branch} 失败：{back.detail}")
        return await git.rollback_to(commit, cwd=REPO_DIR)

    @classmethod
    async def _switch_to(cls, branch: str, label: str) -> str:
        """切到指定分支并重置到其最新提交；失败回滚，成功后按需补装依赖。"""
        previous = await git.head_commit(REPO_DIR)
        previous_branch = await git.current_branch(REPO_DIR)

        stash_created, stash_mark = await _stash_local_changes()
        notes: list[str] = []

        fetched = await git.fetch_all(REPO_DIR)
        if not fetched.ok:
            log.error(f"git fetch 失败：{fetched.detail}")
            return f"更新失败:\n{fetched.detail}"

        upstream = f"origin/{branch}"
        # 先切分支（本地没有就以远程分支为起点建），再把本地分支对齐到远程最新
        checked_out = await git.checkout_or_create(branch, upstream, cwd=REPO_DIR)
        if not checked_out.ok:
            log.error(f"切换分支 {branch} 失败：{checked_out.detail}")
            if await cls._restore(previous, previous_branch):
                return f"更新失败:\n{checked_out.detail}\n已回到更新前的分支与版本"
            return f"更新失败:\n{checked_out.detail}"

        reset = await git.reset_branch_to(branch, upstream, cwd=REPO_DIR)
        if not reset.ok:
            log.error(f"git reset 失败：{reset.detail}")
            if await cls._restore(previous, previous_branch):
                return f"更新失败:\n{reset.detail}\n已回滚到更新前的版本"
            if stash_created:
                notes.append(f"本地改动仍在 stash：{stash_mark}")
            joined = "\n".join(notes)
            return f"更新失败:\n{reset.detail}" + (f"\n{joined}" if joined else "")

        if stash_created:
            notes.append(
                f"本地改动已备份到 stash：{stash_mark}（需要时用 git stash pop 恢复）"
            )

        current = await git.head_commit(REPO_DIR)
        if in_container():
            # 容器里不装依赖：重启时启动脚本按 poetry.lock 对齐，那才是正路。
            notes.append("容器部署：依赖会在下次启动时按 poetry.lock 自动对齐")
            result = "更新完成，请手动重启（发送 /重启 即可）"
        else:
            deps = await _ensure_dependencies(previous, current)
            if deps.changed and deps.ok:
                notes.append("依赖已更新")
            elif not deps.ok:
                notes.append(deps.message)
                log.warning("更新已完成，但依赖未就绪")
            result = "更新完成，请手动重新启动"

        if in_container() and not code_mounted():
            notes.append(
                "注意：代码不在挂载卷上，重建容器后这次更新会丢失"
                "（用 docker compose build 固化到镜像）"
            )

        log.info(f"已更新到 {label}")
        if notes:
            result += "\n" + "\n".join(notes)
        return result
