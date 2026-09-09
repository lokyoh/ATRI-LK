from datetime import datetime, timezone

import pytz

from ATRI.log import log

from . import request

REPO_RELEASE_URL = "https://api.github.com/repos/lokyoh/ATRI-LK/releases"


class ReleaseInfo:
    def __init__(self, release_data):
        self.version = release_data["name"]
        self.tag_name = release_data.get("tag_name") or self.version
        update_time = release_data["updated_at"]
        update_time = update_time.replace("Z", "")
        utc_datetime = datetime.fromisoformat(update_time).replace(tzinfo=timezone.utc)
        shanghai_datetime = utc_datetime.astimezone(pytz.timezone("Asia/Shanghai"))
        self.update_time = shanghai_datetime.strftime("%Y-%m-%d %H:%M")
        self.info = release_data["body"]


class CheckUpdate:
    @staticmethod
    async def _get_release_info() -> dict:
        req = await request.get(REPO_RELEASE_URL)
        return req.json()

    @classmethod
    async def get_latest_info(cls) -> ReleaseInfo | None:
        try:
            data = await cls._get_release_info()
        except Exception:
            log.error("获取发布列表失败...")
            return None
        try:
            release_data: dict = data[0]
        except Exception:
            log.error("GitHub 数据结构已更改, 请前往仓库提交 Issue.")
            return None
        return ReleaseInfo(release_data)


def get_version_num(v: str) -> int:
    try:
        return int(v.replace("Release", "Patch0").replace("Patch", ""))
    except ValueError:
        return 0


def is_newer_version(
    latest_version: str, current_version: str, current_sub_version: str
) -> bool:
    if not latest_version:
        return False
    latest_main_version = latest_version[:11]
    if latest_main_version > current_version:
        return True
    if latest_main_version != current_version:
        return False
    if latest_version == f"{current_version} {current_sub_version}":
        return False
    if current_sub_version[:3] == "Pre":
        return True
    return get_version_num(latest_version[12:]) > get_version_num(current_sub_version)
