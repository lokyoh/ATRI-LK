import asyncio
import re
from datetime import datetime, timezone
import pytz

from ATRI import __version__, __sub_version__
from ATRI.log import log
from ATRI.utils import request
from ATRI.message import MessageBuilder

REPO_COMMITS_URL = "https://api.github.com/repos/lokyoh/ATRI-LK/commits"
REPO_RELEASE_URL = "https://api.github.com/repos/lokyoh/ATRI-LK/releases"


class Updater:
    @staticmethod
    async def _get_commits_info() -> dict:
        req = await request.get(REPO_COMMITS_URL)
        return req.json()

    @staticmethod
    async def _get_release_info() -> dict:
        req = await request.get(REPO_RELEASE_URL)
        return req.json()

    @classmethod
    async def show_latest_commit_info(cls) -> tuple | None:
        try:
            data = await cls._get_commits_info()
        except Exception:
            return None
        try:
            commit_data: dict = data[0]
        except Exception:
            return None
        c_info = commit_data["commit"]
        c_msg = c_info["message"]
        c_time = c_info["author"]["date"]
        c_time = c_time.replace('Z', '')
        utc_datetime = datetime.fromisoformat(c_time).replace(tzinfo=timezone.utc)
        shanghai_datetime = utc_datetime.astimezone(pytz.timezone("Asia/Shanghai"))
        c_time = shanghai_datetime.strftime("%Y-%m-%d %H:%M")
        try:
            r = re.match(r"(YHN-LK.-...) (.*?):", c_msg)
            version = r[1]
            sub_version = r[2]
            if __sub_version__ != version:
                return [version, sub_version], f"提交信息: {c_msg}\n提交时间: {c_time}"
        except Exception:
            pass
        return None, f"提交信息: {c_msg}\n提交时间: {c_time}"

    @classmethod
    async def show_latest_version(cls) -> tuple:
        try:
            data = await cls._get_release_info()
        except Exception:
            return str(), str()
        try:
            release_data: dict = data[0]
        except Exception:
            return str(), str()
        l_v = release_data["tag_name"]
        l_v_t = release_data["published_at"]
        return l_v, l_v_t

    @classmethod
    async def check(cls):
        message = MessageBuilder().text(f"当前版本: {__version__}")
        l_v, l_v_t = await cls.show_latest_version()
        if l_v and l_v_t:
            if l_v[:11] > __version__[:11]:
                message.text(f"新版本已发布,请更新\n最新版本: {l_v}\n更新时间: {l_v_t}")
            else:
                message.text(f"最新版本: {l_v}")
                vs, info = await cls.show_latest_commit_info()
                if info:
                    if vs and vs[1] != __sub_version__:
                        message.text(f"请更新{vs[0]}最新补丁: {vs[1]}")
                        message.text(info)
                    message.text(info)
                else:
                    message.text("检查补丁版本失败")
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
            log.error(f"{e}:{e.args}")
            return "更新失败"
