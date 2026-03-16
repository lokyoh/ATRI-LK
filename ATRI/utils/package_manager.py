"""
包管理器模块
负责 Python软件包的安装与卸载
"""

import subprocess
import sys
from typing import List

from ATRI.log import log as logger


class PackageManager:
    """Python 包管理器，负责安装和卸载 Python软件包"""

    def __init__(self, executable: str = None):
        """
        初始化包管理器

        Args:
            executable: Python 可执行文件路径，默认为当前 Python 环境
        """
        self.executable = executable or sys.executable

    def _run_command(self, command: List[str]) -> tuple[bool, str]:
        """
        运行命令并返回执行结果

        Args:
            command: 命令列表

        Returns:
            (成功标志，输出信息) 的元组
        """
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, check=False
            )

            output = result.stdout.strip() if result.stdout else ""
            error = result.stderr.strip() if result.stderr else ""

            if result.returncode == 0:
                logger.debug(f"命令执行成功：{' '.join(command)}")
                return True, output if output else "命令执行成功"
            else:
                logger.error(f"命令执行失败：{' '.join(command)}")
                logger.error(f"错误输出：{error}")
                return False, error if error else "命令执行失败"

        except Exception as e:
            logger.error(f"执行命令时发生异常：{' '.join(command)}, 错误：{e}")
            return False, str(e)

    def install(self, packages: List[str], upgrade: bool = False) -> tuple[bool, str]:
        """
        安装 Python软件包

        Args:
            packages: 要安装的软件包列表
            upgrade: 是否升级到最新版本

        Returns:
            (成功标志，输出信息) 的元组
        """
        if not packages:
            logger.warning("没有指定要安装的软件包")
            return False, "没有指定要安装的软件包"

        command = [self.executable, "-m", "pip", "install"]

        if upgrade:
            command.append("--upgrade")

        command.extend(packages)

        logger.info(f"开始安装包：{packages}")
        return self._run_command(command)

    def uninstall(self, packages: List[str], yes: bool = True) -> tuple[bool, str]:
        """
        卸载 Python软件包

        Args:
            packages: 要卸载的软件包列表
            yes: 是否自动确认卸载（不提示确认）

        Returns:
            (成功标志，输出信息) 的元组
        """
        if not packages:
            logger.warning("没有指定要卸载的软件包")
            return False, "没有指定要卸载的软件包"

        command = [self.executable, "-m", "pip", "uninstall"]

        if yes:
            command.append("-y")

        command.extend(packages)

        logger.info(f"开始卸载包：{packages}")
        return self._run_command(command)

    def install_requirements(self, requirements_file: str) -> tuple[bool, str]:
        """
        从 requirements 文件安装依赖

        Args:
            requirements_file: requirements.txt 文件路径

        Returns:
            (成功标志，输出信息) 的元组
        """
        command = [self.executable, "-m", "pip", "install", "-r", requirements_file]

        logger.info(f"从文件安装依赖：{requirements_file}")
        return self._run_command(command)

    def freeze(self) -> List[str]:
        """
        获取已安装的包列表

        Returns:
            已安装的包列表，格式为 ['package==version', ...]
        """
        try:
            result = subprocess.run(
                [self.executable, "-m", "pip", "freeze"],
                capture_output=True,
                text=True,
                check=True,
            )

            packages = (
                result.stdout.strip().split("\n") if result.stdout.strip() else []
            )
            logger.debug(f"获取到 {len(packages)} 个已安装的包")
            return packages

        except Exception as e:
            logger.error(f"获取已安装包列表失败：{e}")
            return []

    def list_installed(self) -> List[dict]:
        """
        获取已安装的包详细信息

        Returns:
            已安装的包信息列表，每个包包含 name 和 version
        """
        packages = self.freeze()
        result = []

        for pkg in packages:
            if "==" in pkg:
                name, version = pkg.split("==", 1)
                result.append({"name": name, "version": version})

        return result

    def search(self, package_name: str) -> tuple[bool, str]:
        """
        搜索远程软件包

        Args:
            package_name: 要搜索的包名

        Returns:
            (成功标志，输出信息) 的元组
        """
        command = [self.executable, "-m", "pip", "search", package_name]
        logger.info(f"搜索包：{package_name}")
        return self._run_command(command)

    def show(self, package_name: str) -> str:
        """
        显示包的详细信息

        Args:
            package_name: 包名

        Returns:
            包的详细信息字符串
        """
        try:
            result = subprocess.run(
                [self.executable, "-m", "pip", "show", package_name],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode == 0:
                return result.stdout
            else:
                logger.warning(f"未找到包：{package_name}")
                return ""

        except Exception as e:
            logger.error(f"查看包信息失败：{e}")
            return ""
