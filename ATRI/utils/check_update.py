from datetime import datetime, timezone
import pytz

from ATRI.log import log

from . import request

REPO_COMMITS_URL = "https://api.github.com/repos/lokyoh/ATRI-LK/commits"
REPO_RELEASE_URL = "https://api.github.com/repos/lokyoh/ATRI-LK/releases"


class CheckUpdate:
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
            log.error("获取最新推送信息失败...")
            return None

        try:
            commit_data: dict = data[0]
        except Exception:
            log.error("GitHub 数据结构已更改, 请前往仓库提交 Issue.")
            return None

        c_info = commit_data["commit"]
        c_msg = c_info["message"]
        c_sha = commit_data["sha"][0:5]
        c_time = c_info["author"]["date"]
        c_time = c_time.replace('Z', '')
        utc_datetime = datetime.fromisoformat(c_time).replace(tzinfo=timezone.utc)
        shanghai_datetime = utc_datetime.astimezone(pytz.timezone("Asia/Shanghai"))
        c_time = shanghai_datetime.strftime("%Y-%m-%d %H:%M")

        return c_msg, c_sha, c_time

    @classmethod
    async def show_latest_version(cls) -> tuple:
        try:
            data = await cls._get_release_info()
        except Exception:
            log.error("获取发布列表失败...")
            return str(), str()

        try:
            release_data: dict = data[0]
        except Exception:
            log.error("GitHub 数据结构已更改, 请前往仓库提交 Issue.")
            return str(), str()

        l_v = release_data["name"]
        l_v_t = release_data["updated_at"]
        l_v_t = l_v_t.replace('Z', '')
        utc_datetime = datetime.fromisoformat(l_v_t).replace(tzinfo=timezone.utc)
        shanghai_datetime = utc_datetime.astimezone(pytz.timezone("Asia/Shanghai"))
        l_v_t = shanghai_datetime.strftime("%Y-%m-%d %H:%M")
        return l_v, l_v_t


def get_version_num(v: str):
    try:
        return int(v.replace("Release", "Patch0").replace("Patch", ""))
    except ValueError:
        return 0
