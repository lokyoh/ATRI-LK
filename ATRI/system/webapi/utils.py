import os
import re
from datetime import datetime
from pathlib import Path

import psutil
from nonebot.utils import run_sync

from .base_model import SystemFolderSize, SystemStatus


def validate_path(path_str: str | None) -> tuple[Path | None, str | None]:
    """验证路径是否安全

    参数:
        path_str: 用户输入的路径

    返回:
        tuple[Path | None, str | None]: (验证后的路径, 错误信息)
    """
    try:
        if not path_str:
            return Path().resolve(), None

        # 1. 移除任何可能的路径遍历尝试
        path_str = re.sub(r"[\\/]\.\.[\\/]", "", path_str)

        # 2. 规范化路径并转换为绝对路径
        path = Path(path_str).resolve()

        # 3. 获取项目根目录
        root_dir = Path().resolve()

        # 4. 验证路径是否在项目根目录内
        try:
            if not path.is_relative_to(root_dir):
                return None, "访问路径超出允许范围"
        except ValueError:
            return None, "无效的路径格式"

        # 5. 验证路径是否包含任何危险字符
        if any(c in str(path) for c in ["..", "~", "*", "?", ">", "<", "|", '"']):
            return None, "路径包含非法字符"

        # 6. 验证路径长度是否合理
        return (None, "路径长度超出限制") if len(str(path)) > 4096 else (path, None)
    except Exception as e:
        return None, f"路径验证失败: {e!s}"


def _get_dir_size(dir_path: Path) -> float:
    """获取文件夹大小

    参数:
        dir_path: 文件夹路径
    """
    return sum(
        sum(os.path.getsize(os.path.join(root, name)) for name in files)
        for root, dirs, files in os.walk(dir_path)
    )


@run_sync
def get_system_status() -> SystemStatus:
    """获取系统信息等"""
    cpu = psutil.cpu_percent()
    memory = psutil.virtual_memory().percent
    disk = psutil.disk_usage("/").percent
    return SystemStatus(
        cpu=cpu,
        memory=memory,
        disk=disk,
        check_time=datetime.now().replace(microsecond=0),
    )


@run_sync
def get_system_disk(
    full_path: str | None,
) -> list[SystemFolderSize]:
    """获取资源文件大小等"""
    base_path = Path(full_path) if full_path else Path()
    other_size = 0
    data_list = []
    for file in os.listdir(base_path):
        f = base_path / file
        if f.is_dir():
            size = _get_dir_size(f) / 1024 / 1024
            data_list.append(
                SystemFolderSize(name=file, size=size, full_path=str(f), is_dir=True)
            )
        else:
            other_size += f.stat().st_size / 1024 / 1024
    if other_size:
        data_list.append(
            SystemFolderSize(
                name="other_file", size=other_size, full_path=full_path, is_dir=False
            )
        )
    return data_list
