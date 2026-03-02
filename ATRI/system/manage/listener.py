import json

from nonebot.adapters.onebot.v11 import (
    Event,
    MessageEvent,
)
from nonebot.exception import IgnoredException
from nonebot.matcher import Matcher
from nonebot.message import run_preprocessor
from sympy import preorder_traversal

from ATRI.service import ServiceTools

from .data_source import MANAGE_DIR


@run_preprocessor
async def _(matcher: Matcher, event: MessageEvent):
    plugin_name = str(matcher.plugin_name)
    if "nonebot_" not in plugin_name:
        return
    serv = ServiceTools(plugin_name)
    try:
        serv.load_service_config()
    except Exception:
        raise IgnoredException(f"{plugin_name} limited")
    if not serv.auth_service():
        raise IgnoredException(f"{plugin_name} limited")
    user_id = str(getattr(event, "user_id", None))
    group_id = str(getattr(event, "group_id", None))
    result = serv.auth_service(user_id, group_id)
    if not result:
        raise IgnoredException(f"{plugin_name} limited")


@run_preprocessor
async def _(event: Event):
    user_id = str(getattr(event, "user_id", ""))
    group_id = str(getattr(event, "group_id", ""))

    if user_id:
        blockuser_file_path = MANAGE_DIR / "block_user.json"
        if not blockuser_file_path.is_file():
            with open(blockuser_file_path, "w", encoding="utf-8") as w:
                w.write(json.dumps(dict()))
        data = json.loads(blockuser_file_path.read_bytes())
        if user_id in data:
            raise IgnoredException(f"Blocked user: {user_id}")

    if group_id:
        blockgroup_file_path = MANAGE_DIR / "block_group.json"
        if not blockgroup_file_path.is_file():
            with open(blockgroup_file_path, "w", encoding="utf-8") as w:
                w.write(json.dumps(dict()))
        data = json.loads(blockgroup_file_path.read_bytes())
        if group_id in data:
            raise IgnoredException(f"Blocked group: {group_id}")


def init_listener():
    """初始化监听器"""
