import asyncio
from dataclasses import dataclass
from pathlib import Path

from ATRI.log import log


@dataclass
class GitResult:
    ok: bool
    """git 是否成功（以退出码为准）"""
    stdout: str
    stderr: str
    command: str = ""

    @property
    def detail(self) -> str:
        return (self.stderr or self.stdout).strip()

    def __bool__(self) -> bool:
        return self.ok


async def run_git(*args: str, cwd: str | Path | None = None) -> GitResult:
    """执行一条 git 命令，**按退出码判断成败**。

    用 ``create_subprocess_exec`` 传参数列表而非拼 shell 字符串：仓库地址、
    插件目录名来自外部，走 shell 会多一层解析与注入面。

    不按 stderr 是否为空判断成败：``git fetch`` / ``git clone`` 在真实远程下
    会把进度写进 stderr（``From github.com:...``），成功也会有输出。
    """
    command = "git " + " ".join(args)
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(cwd) if cwd is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()
    except FileNotFoundError:
        return GitResult(
            ok=False,
            stdout="",
            stderr="未找到 git 命令，请确认已安装 git",
            command=command,
        )
    except Exception as e:
        return GitResult(ok=False, stdout="", stderr=str(e), command=command)

    return GitResult(
        ok=proc.returncode == 0,
        stdout=stdout_b.decode(errors="replace"),
        stderr=stderr_b.decode(errors="replace"),
        command=command,
    )


async def is_git_repo(cwd: str | Path | None = None) -> bool:
    result = await run_git("rev-parse", "--is-inside-work-tree", cwd=cwd)
    return result.ok and result.stdout.strip() == "true"


async def head_commit(cwd: str | Path | None = None) -> str:
    """当前 HEAD 的 commit；取不到返回空串（调用方据此决定能否回滚）。"""
    result = await run_git("rev-parse", "HEAD", cwd=cwd)
    return result.stdout.strip() if result.ok else ""


async def clone(repo: str, target: str | Path):
    return await run_git("clone", repo, str(target))


async def pull(cwd: str | Path):
    return await run_git("pull", "--force", cwd=cwd)


async def fetch_all(cwd: str | Path | None = None, tags: bool = True):
    args = ["fetch", "--all", "-f"]
    if tags:
        args.insert(2, "--tags")
    return await run_git(*args, cwd=cwd)


async def fetch_branch(branch: str, cwd: str | Path | None = None):
    """只取目标分支。

    更新流程只用到 ``origin/<branch>``，``fetch --all --tags`` 会把所有分支和
    标签都拉一遍；仓库大、分支多时这一步很显眼。分支不存在时退出码非 0，
    由调用方按失败处理。
    """
    return await run_git("fetch", "--no-tags", "origin", branch, cwd=cwd)


async def reset_hard(target: str, cwd: str | Path | None = None):
    return await run_git("reset", "--hard", target, cwd=cwd)


async def checkout_branch(branch: str, cwd: str | Path | None = None):
    """切到指定分支。

    ``-B`` 会在分支不存在时新建并指向目标起点，避免「本地无此分支」时报错。
    已有同名分支则直接切换（不强制重建，保留其跟踪关系）。
    """
    return await run_git("checkout", branch, cwd=cwd)


async def checkout_or_create(branch: str, start: str, cwd: str | Path | None = None):
    """切到分支；本地不存在时以 ``start`` 为起点创建并跟踪它。"""
    return await run_git("checkout", "-B", branch, start, cwd=cwd)


async def reset_branch_to(branch: str, upstream: str, cwd: str | Path | None = None):
    """把本地分支硬重置到 ``upstream``（通常是一个远程跟踪分支）。"""
    return await run_git("reset", "--hard", upstream, cwd=cwd)


async def has_local_changes(
    cwd: str | Path | None = None, *, untracked: bool = False
) -> bool:
    """工作区里有没有本地改动。

    :param untracked: 是否把**未跟踪文件**也算作改动。默认不算：``git reset
        --hard`` 只覆盖已跟踪的文件，未跟踪文件本来就不会丢；而为了它们去跑
        ``stash --include-untracked`` 会把整个目录拷进 ``.git``——插件商店装出来
        的目录动辄上百 MB，更新就会卡在备份这一步。
    """
    args = ["status", "--porcelain"]
    if not untracked:
        args.append("--untracked-files=no")
    result = await run_git(*args, cwd=cwd)
    return bool(result.stdout.strip()) if result.ok else False


async def stash_local_changes(
    cwd: str | Path | None = None,
    message: str = "",
    *,
    include_untracked: bool = False,
) -> tuple[bool, str]:
    """把未提交改动收进 stash，避免 reset --hard 丢掉。

    默认只备份**已跟踪文件**的改动。未跟踪文件不备份，一是 ``reset --hard``
    不会删它们，二是备份成本极高（大目录逐个拷贝）。

    返回 ``(是否创建了 stash, stash 标记)``；没有改动时不创建空 stash。
    """
    if not await has_local_changes(cwd, untracked=include_untracked):
        return False, ""
    mark = message or "atri-auto"
    args = ["stash", "push"]
    if include_untracked:
        args.append("--include-untracked")
    args += ["-m", mark]
    result = await run_git(*args, cwd=cwd)
    if not result.ok:
        log.warning(f"git stash 失败：{result.detail}")
        return False, ""
    return True, mark


async def rollback_to(commit: str, cwd: str | Path | None = None) -> bool:
    """回滚到指定 commit；失败只记日志，不抛异常。"""
    if not commit:
        log.warning("没有记录到用于回滚的 commit，跳过回滚")
        return False
    result = await reset_hard(commit, cwd=cwd)
    if result.ok:
        log.warning(f"已回滚到 {commit[:8]}")
        return True
    log.error(f"回滚失败：{result.detail}")
    return False


async def current_branch(cwd: str | Path | None = None) -> str:
    result = await run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
    return result.stdout.strip() if result.ok else ""
