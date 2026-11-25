import os
import sys
from pathlib import Path

CONTAINER_MARKERS = ("/.dockerenv", "/run/.containerenv")
"""容器运行时留下的标记文件。"""

ENTRYPOINT_ENV = "ATRI_ENTRYPOINT"
"""启动脚本路径的环境变量名；Dockerfile 会把它指向 docker_entrypoint.py。"""


def in_container() -> bool:
    """是否跑在容器里。

    判断依据按可靠度排列：标记文件 → Kubernetes 注入的环境变量 → 显式开关
    ``ATRI_IN_CONTAINER=1``（没有标记文件的运行时可用它兜底）。
    """
    for marker in CONTAINER_MARKERS:
        if Path(marker).exists():
            return True
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        return True
    return os.environ.get("ATRI_IN_CONTAINER", "").strip() == "1"


def entrypoint_path() -> Path | None:
    """启动脚本的实际路径；没有配置或文件不存在时返回 None。"""
    value = os.environ.get(ENTRYPOINT_ENV, "").strip()
    if not value:
        return None
    path = Path(value)
    return path if path.is_file() else None


def _mount_points() -> set[str]:
    """当前进程可见的挂载点（读不到就返回空集合）。"""
    points: set[str] = set()
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as handle:
            for line in handle:
                fields = line.split()
                if len(fields) > 4:
                    points.add(fields[4])
    except OSError:
        return set()
    return points


def code_mounted() -> bool:
    """代码是不是来自挂载目录。

    挂载目录里的代码改完就留在宿主机上，重建容器也还在；没挂载的话改的是容器
    可写层，``docker compose up`` 重建一次就没了——这种情况要在回复里提醒。
    """
    try:
        target = str(Path.cwd().resolve())
    except OSError:
        return False
    return target in _mount_points()


def restart_argv() -> list[str]:
    """重启时要执行的命令（``/重启`` 用）。

    容器里优先重新执行启动脚本：它会先按锁文件对齐依赖再跑应用。重启仍然由
    用户主动发起，只是「重启」这一步顺带把依赖补上，不需要用户再敲 docker 命令。
    """
    entry = entrypoint_path()
    if entry is not None:
        return [sys.executable, str(entry)]
    return [sys.executable, *sys.argv]


def update_hint() -> str:
    """无法自动重启时，告诉用户去宿主机怎么做。"""
    if code_mounted():
        steps = (
            "1. git pull（仓库已挂进容器，宿主机拉取后重启即生效）\n"
            "2. docker compose restart（启动脚本会按 poetry.lock 自动对齐依赖）"
        )
    else:
        steps = (
            "当前代码不在挂载目录上，重建容器会丢失改动，所以要在宿主机重建镜像：\n"
            "docker compose build && docker compose up -d"
        )
    return (
        "当前是容器部署，但没找到启动脚本（ATRI_ENTRYPOINT），无法自动完成重启。\n"
        f"请在宿主机执行：\n{steps}"
    )
