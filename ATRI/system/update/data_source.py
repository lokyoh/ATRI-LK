import asyncio
import shutil
from dataclasses import dataclass

from ATRI import __sub_version__, __version__
from ATRI.log import log
from ATRI.message import MessageBuilder
from ATRI.utils import git
from ATRI.utils.check_update import CheckUpdate, is_newer_version

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
            return message
        else:
            return message.text("最新版本获取失败")

    @classmethod
    async def update(cls):
        """更新到最新正式版。"""
        if not await git.is_git_repo(REPO_DIR):
            return "当前不是 git 仓库，无法自动更新（请手动替换文件）"
        latest_info = await CheckUpdate.get_latest_info()
        if latest_info is None or not latest_info.tag_name:
            return "获取最新发布信息失败"
        return await cls._switch_to(
            latest_info.tag_name, f"正式版 {latest_info.version}"
        )

    @classmethod
    async def update_to_beta(cls):
        """更新到 main 分支最新提交。"""
        if not await git.is_git_repo(REPO_DIR):
            return "当前不是 git 仓库，无法自动更新（请手动替换文件）"
        return await cls._switch_to("origin/main", "测试版 origin/main")

    @classmethod
    async def _switch_to(cls, target: str, label: str) -> str:
        """切到指定 ref；失败回滚，成功后按需补装依赖。"""
        previous = await git.head_commit(REPO_DIR)

        stash_created, stash_mark = await _stash_local_changes()
        notes: list[str] = []

        fetched = await git.fetch_all(REPO_DIR)
        if not fetched.ok:
            log.error(f"git fetch 失败：{fetched.detail}")
            return f"更新失败:\n{fetched.detail}"

        reset = await git.reset_hard(target, cwd=REPO_DIR)
        if not reset.ok:
            log.error(f"git reset 失败：{reset.detail}")
            if await git.rollback_to(previous, cwd=REPO_DIR):
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
        deps = await _ensure_dependencies(previous, current)
        if deps.changed and deps.ok:
            notes.append("依赖已更新")
        elif not deps.ok:
            notes.append(deps.message)
            log.warning("更新已完成，但依赖未就绪")

        log.info(f"已更新到 {label}")
        result = "更新完成，请手动重新启动"
        if notes:
            result += "\n" + "\n".join(notes)
        return result
