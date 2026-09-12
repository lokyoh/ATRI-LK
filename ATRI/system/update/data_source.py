import asyncio

from ATRI import __sub_version__, __version__
from ATRI.exceptions import str_traceback
from ATRI.log import log
from ATRI.message import MessageBuilder
from ATRI.utils.check_update import CheckUpdate, is_newer_version


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
        try:
            latest_info = await CheckUpdate.get_latest_info()
            if latest_info is None or not latest_info.tag_name:
                return "获取最新发布信息失败"
            proc = await asyncio.create_subprocess_exec(
                "git",
                "fetch",
                "--all",
                "--tags",
                "-f",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr1 = await proc.communicate()
            if proc.returncode != 0:
                return f"更新失败:\n{stderr1.decode(errors='replace')}"
            proc = await asyncio.create_subprocess_exec(
                "git",
                "reset",
                "--hard",
                latest_info.tag_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr2 = await proc.communicate()
            if proc.returncode != 0:
                return f"更新失败:\n{stderr2.decode(errors='replace')}"
            return "更新完成，请手动重新启动"
        except Exception as e:
            log.error(f"更新失败:\n{str_traceback(e)}")
            return "更新失败"

    @classmethod
    async def update_to_beta(cls):
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "fetch", "--all", "-f", stdout=asyncio.subprocess.PIPE
            )
            _, stderr1 = await proc.communicate()
            proc = await asyncio.create_subprocess_exec(
                "git", "reset", "--hard", "origin/main", stdout=asyncio.subprocess.PIPE
            )
            _, stderr2 = await proc.communicate()
            if stderr1:
                err = stderr1
                if stderr2:
                    err += f"\n{stderr2}"
                return f"更新失败:\n{err}"
            return "更新完成，请重新启动"
        except Exception as e:
            log.error(f"更新失败:\n{str_traceback(e)}")
            return "更新失败"
