"""容器启动前对齐依赖，然后启动应用。

容器部署的代码来自宿主机（compose 把仓库挂进 ``/app``），依赖装在一个命名卷
里。宿主机 ``git pull`` 之后 ``pyproject.toml`` / ``poetry.lock`` 可能变了，而
依赖卷还是旧的——这时候容器重启会直接 import 失败。所以启动前比对一次锁文件
指纹，**只有变了才真的装**。

镜像只装**运行环境**（虚拟环境、浏览器、启动脚本），代码由宿主机挂载到 /app。
虚拟环境刻意放在 /opt/atri-venv：compose 把宿主机仓库整个挂到 /app，放在 /app
里面会被挂载遮住（或者反过来把 venv 写进宿主机仓库）。

为什么不在更新插件里装：更新插件跑在容器内部，让它在容器里改自己的代码和依赖，
等于篡改部署产物——镜像里是什么、实际跑的是什么，从此对不上，出了问题也没法
复现。对齐依赖属于「启动的一部分」，只在这里做。

用法：

    python docker_entrypoint.py              # 正常启动
    python docker_entrypoint.py --mark-only  # 只记录当前指纹（构建镜像时用）
    python docker_entrypoint.py --check      # 只打印状态，不安装
"""

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

#: 与 Dockerfile 的 ``POETRY_VERSION`` 保持一致。
POETRY_SPEC = "poetry==2.3.2"

#: 与 Dockerfile 的安装参数一致：只装主依赖，不装项目自身。
INSTALL_ARGS = (
    "install",
    "--no-interaction",
    "--no-ansi",
    "--no-root",
    "--only",
    "main",
)

#: 判断「依赖是否需要重装」的文件。
LOCK_FILES = ("pyproject.toml", "poetry.lock")

#: 指纹存在虚拟环境里——依赖和指纹同生共死，不会出现「标记是新的、包装的是旧的」。
MARKER_NAME = ".atri-deps.hash"

MAIN_SCRIPT = "main.py"

#: 本脚本自己的开关，不传给应用。
ENTRY_FLAGS = ("--check", "--mark-only")


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name, "").strip()
    return Path(value) if value else default


def app_dir() -> Path:
    return _env_path("ATRI_APP_DIR", Path("/app"))


def venv_dir() -> Path:
    """应用虚拟环境的位置。

    默认在 ``/opt/atri-venv``，**不在** ``/app`` 下：``/app`` 通常被宿主机仓库
    整个挂载，放里面要么被遮住，要么把 venv 写进宿主机仓库。
    """
    return _env_path("ATRI_VENV_DIR", Path("/opt/atri-venv"))


def tool_dir() -> Path:
    """镜像里预装的 poetry（构建阶段做好，运行期零下载）。"""
    return _env_path("ATRI_POETRY_TOOL", Path("/opt/poetry-tool"))


def bootstrap_dir() -> Path:
    """镜像里没带 poetry 时的自举位置。放 data/ 下，重启后还在。"""
    return _env_path("ATRI_POETRY_BOOTSTRAP", app_dir() / "data" / "tools" / "poetry")


def _bin_dir(directory: Path) -> Path:
    return directory / ("Scripts" if os.name == "nt" else "bin")


def _exe(directory: Path, name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    return _bin_dir(directory) / f"{name}{suffix}"


def venv_python() -> Path:
    return _exe(venv_dir(), "python")


def marker_file() -> Path:
    return venv_dir() / MARKER_NAME


def log(message: str) -> None:
    print(f"[entrypoint] {message}", flush=True)


def run(cmd, *, cwd=None, env=None) -> int:
    log("$ " + " ".join(str(part) for part in cmd))
    try:
        return subprocess.run(
            [str(part) for part in cmd],
            cwd=str(cwd or app_dir()),
            env=env,
            check=False,
        ).returncode
    except OSError as exc:
        log(f"命令执行失败：{exc}")
        return 1


def app_code_present() -> bool:
    """``/app`` 里到底有没有代码。

    镜像只带运行环境（依赖 + 浏览器 + 启动脚本），代码由宿主机挂进 ``/app``。
    没挂上就启动只会得到一堆 import 错误，不如直接说清楚该挂什么。
    """
    return (app_dir() / MAIN_SCRIPT).exists() and (
        app_dir() / "pyproject.toml"
    ).exists()


def lock_fingerprint() -> str:
    """锁文件指纹；读不到依赖清单时返回空串。

    把 poetry 版本也混进哈希：换了 poetry 的版本，同一份锁也可能装出不同结果。
    """
    digest = hashlib.sha256(POETRY_SPEC.encode())
    for name in LOCK_FILES:
        path = app_dir() / name
        try:
            digest.update(path.read_bytes())
        except OSError:
            return ""
    return digest.hexdigest()[:16]


def read_marker() -> str:
    try:
        return marker_file().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_marker(fingerprint: str) -> bool:
    if not fingerprint:
        return False
    try:
        marker_file().parent.mkdir(parents=True, exist_ok=True)
        marker_file().write_text(fingerprint + "\n", encoding="utf-8")
        return True
    except OSError as exc:
        log(f"写入依赖指纹失败（忽略）：{exc}")
        return False


def ensure_venv() -> bool:
    """依赖卷为空时把虚拟环境建出来。

    命名卷第一次挂载时由镜像内容填充，所以正常情况这里不会触发；留这一手是防
    「卷是空的但容器照样起来」——那时候至少能自己救回来。
    """
    if venv_python().exists():
        return True
    log(f"依赖卷里没有虚拟环境，创建：{venv_dir()}")
    return run([sys.executable, "-m", "venv", str(venv_dir())]) == 0


def find_poetry() -> str | None:
    """按顺序找 poetry：镜像预装 → 之前的自举 → PATH。"""
    for directory in (tool_dir(), bootstrap_dir()):
        candidate = _exe(directory, "poetry")
        if candidate.exists():
            return str(candidate)
    found = shutil.which("poetry")
    return found


def bootstrap_poetry() -> str | None:
    """镜像没带 poetry 时自举一份（独立性同 tool_dir：不碰应用依赖）。"""
    target = bootstrap_dir()
    python = _exe(target, "python")
    if not python.exists():
        log(f"创建 poetry 工具环境：{target}")
        if run([sys.executable, "-m", "venv", str(target)]) != 0:
            return None
    log(f"安装 {POETRY_SPEC}（仅用于对齐依赖）...")
    code = run(
        [
            python,
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "--disable-pip-version-check",
            POETRY_SPEC,
        ]
    )
    if code != 0:
        return None
    return str(_exe(target, "poetry"))


def poetry_env() -> dict:
    """让 poetry 装进**这个**依赖卷里的虚拟环境，而不是另建一个。"""
    env = dict(os.environ)
    env["VIRTUAL_ENV"] = str(venv_dir())
    env["PATH"] = os.pathsep.join(
        [str(_bin_dir(venv_dir())), env.get("PATH", "")]
    ).strip(os.pathsep)
    env.pop("POETRY_VIRTUALENVS_PATH", None)
    return env


def _failure_hint() -> str:
    return (
        "依赖没能对齐，应用可能因缺包启动失败。排查建议：\n"
        "  - 检查容器能否访问 PyPI；\n"
        "  - 报「锁文件版本不兼容」说明镜像里的 poetry 太旧，"
        "在宿主机 docker compose build 重建一次；\n"
        "  - 临时跳过对齐（确认依赖本来就是新的）：ATRI_SKIP_DEP_SYNC=1。"
    )


def sync_deps() -> bool:
    """依赖卷与锁文件对齐。已经对齐就什么都不做。"""
    if os.environ.get("ATRI_SKIP_DEP_SYNC") == "1":
        log("ATRI_SKIP_DEP_SYNC=1，跳过依赖对齐")
        return True

    fingerprint = lock_fingerprint()
    if not fingerprint:
        log(f"找不到 {LOCK_FILES}，跳过依赖对齐")
        return True
    if read_marker() == fingerprint:
        log("依赖与锁文件一致，跳过安装")
        return True

    log("依赖与锁文件不一致，开始安装...")
    if not ensure_venv():
        log(_failure_hint())
        return False

    poetry = find_poetry() or bootstrap_poetry()
    if not poetry:
        log(_failure_hint())
        return False

    code = run([poetry, *INSTALL_ARGS], env=poetry_env())
    if code != 0:
        log(_failure_hint())
        return False

    write_marker(fingerprint)
    log("依赖安装完成")
    return True


def _launch_command(argv: list[str]) -> list[str]:
    """把命令里的 ``python`` 换成依赖卷里的解释器。

    只剥掉本脚本自己的开关，其它参数原样透传给应用。
    """
    target = str(venv_python() if venv_python().exists() else sys.executable)
    args = [arg for arg in argv if arg not in ENTRY_FLAGS]
    if args and Path(args[0]).name in ("python", "python3"):
        args = args[1:]
    if not args:
        args = [MAIN_SCRIPT]
    return [target, *args]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if "--check" in ENTRY_FLAGS and "--check" in argv:
        fingerprint = lock_fingerprint()
        print(f"app_dir={app_dir()}")
        print(f"venv={venv_dir()}")
        print(f"python={venv_python()} (exists={venv_python().exists()})")
        print(f"poetry={find_poetry()}")
        print(f"fingerprint={fingerprint or '(读不到锁文件)'}")
        print(f"marker={read_marker() or '(空)'}")
        print(f"synced={bool(fingerprint) and read_marker() == fingerprint}")
        return 0

    if "--mark-only" in ENTRY_FLAGS and "--mark-only" in argv:
        fingerprint = lock_fingerprint()
        if not write_marker(fingerprint):
            print("[entrypoint] 记录依赖指纹失败", file=sys.stderr)
            return 1
        print(f"[entrypoint] 已记录依赖指纹：{fingerprint}")
        return 0

    if not app_code_present():
        log(f"没找到应用代码（{app_dir()}）：本镜像只包含运行环境。")
        log("请在 /app 挂载代码仓库；仓库自带的 docker-compose.yml 已配置 ./:/app。")
        return 1

    sync_deps()

    command = _launch_command(argv)
    log("启动：" + " ".join(command))
    try:
        os.execv(command[0], command)
    except OSError as exc:
        log(f"启动失败：{exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
