import asyncio

from ATRI import __version__, __sub_version__
from ATRI.log import log
from ATRI.utils.check_update import CheckUpdate, get_version_num
from ATRI.message import MessageBuilder
from ATRI.exceptions import str_traceback


class Updater:
    @classmethod
    async def check(cls):
        message = MessageBuilder().text(f"当前版本: {__version__} {__sub_version__}")
        l_v, l_v_t = await CheckUpdate.show_latest_version()
        if l_v and l_v_t:
            message.text(f"远程版本: {l_v} 更新时间: {l_v_t}")
            if (
                    l_v[:11] > __version__ or (
                    l_v[:11] == __version__ and l_v != f"{__version__} {__sub_version__}" and (
                    __sub_version__[:3] == "Pre" or get_version_num(l_v[12:]) > get_version_num(__sub_version__)))
            ):
                message.text(f"新版本已发布,请更新!!!")
            info = await CheckUpdate.show_latest_commit_info()
            if info:
                message.text(f"提交信息: {info[0]}\n提交时间: {info[2]}")
            else:
                message.text("提交信息获取失败")
            return message
        else:
            return message.text("最新版本获取失败")

    @classmethod
    async def update(cls):
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
            return f"更新完成，请手动重新启动"
        except Exception as e:
            log.error(f"更新失败:\n{str_traceback(e)}")
            return "更新失败"
