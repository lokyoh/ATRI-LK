"""旧日程接口 facade。

生活系统（`agent/life/`）是唯一实现；这里保留 `ATRISchedule` / `NowSchedule` /
`TodaySchedule` 的旧形状，避免一次性改爆调用方。
"""

from __future__ import annotations

import datetime

from ATRI.log import log
from ATRI.utils.datetime import now, now_time

from .life import EntryKind, LifeSystem, ScheduleEntry, get_life_system, get_now_date

#: 「昨日记忆」注入日程生成的字数预算。
SCHEDULE_MEMORY_BUDGET = 600

#: 「主人信息」注入日程生成的字数预算。
SCHEDULE_PEOPLE_BUDGET = 400


def _master_label(user_id: str) -> str:
    """主人的称呼。取不到名字时退化成「主人」，不要编名字。"""
    try:
        from ATRI.system.lkapi.bot import util as lk_util

        if lk_util.is_valid_user(user_id):
            label = (lk_util.get_name(user_id) or "").strip()
            if label:
                return label
    except Exception:
        pass
    return "主人"


def _describe_people() -> str | None:
    """把**主人**整理成生活/日程生成的参考素材（只写主人，不写其他熟人）。"""
    from .user import list_known_users
    from .util import get_user_group

    try:
        users = list_known_users()
    except Exception:
        users = []

    master_id = ""
    master_relation = ""
    master_profile = ""
    for u in users:
        uid = str(u["user_id"])
        try:
            if get_user_group(uid) != "主人":
                continue
        except Exception:
            continue
        master_id = uid
        master_relation = str(u["relation"] or "")
        master_profile = str(u["profile"] or "")
        break

    if not master_id:
        try:
            from ATRI.permission import get_master_ids

            master_id = next(iter(get_master_ids()), "")
        except Exception:
            master_id = ""
    if not master_id:
        return None

    detail = f"- {_master_label(master_id)}（主人）"
    if master_relation:
        detail += f"：{master_relation}"
    if master_profile:
        detail += f"；你对TA的印象：{master_profile}"
    if len(detail) > SCHEDULE_PEOPLE_BUDGET:
        detail = detail[:SCHEDULE_PEOPLE_BUDGET]

    head = "### 关于主人\n"
    head += "主人是她最重要的人，日程里应当自然出现，而不是用「朋友」泛指。\n"
    head += "日程请围绕她和主人展开（可以一起吃饭、聊天、被叫走）；"
    head += "不要再引入其他具体的人，也不要凭空造一群泛泛的「朋友」。\n"
    return head + detail + "\n\n"


class NowSchedule:
    def __init__(self, today_outfit: str = "Null", now_schedule: str = "Null"):
        self.outfit: str = today_outfit
        self.now_schedule: str = now_schedule


class ScheduleNode:
    def __init__(self, data):
        self.start_time = datetime.time.fromisoformat(data["start_time"])
        self.end_time = datetime.time.fromisoformat(data["end_time"])
        self.schedule: str = data["schedule"]

    @classmethod
    def from_entry(cls, entry: ScheduleEntry) -> ScheduleNode:
        if entry.kind is EntryKind.TRAVEL:
            text = f"前往{entry.to_place}"
        else:
            place = f"{entry.place}：" if entry.place else ""
            text = f"{place}{entry.activity or '空闲'}"
        return cls(
            {
                "start_time": entry.start.isoformat(),
                "end_time": entry.end.isoformat(),
                "schedule": text,
            }
        )


class TodaySchedule:
    """旧 `TodaySchedule` 的只读 adapter；数据来自合并后的生活时间线。"""

    def __init__(self, data=None, life: LifeSystem | None = None):
        self._life = life or get_life_system()
        if isinstance(data, dict) and "schedule" in data:
            self.outfit: str = data.get("outfit", "Null")
            self.schedule: list[ScheduleNode] = [
                ScheduleNode(item) for item in data["schedule"]
            ]
        else:
            self.outfit = self._life.outfits.prompt_text()
            self.schedule = [
                ScheduleNode.from_entry(entry) for entry in self._life.plan()
            ]

    def get_schedule_from_now(self):
        state = self._life.location()
        return state.describe()


class ATRISchedule:
    """兼容入口；真正实现是生活系统。"""

    before_prompt = (
        """请生成亚托莉在虚构世界中的一日详细日程...（旧提示词，已不再使用）"""
    )
    after_prompt = ""

    def __init__(self):
        self.schedule_data: dict = {}
        self.data_file = None

    async def get_schedule(self):
        life = get_life_system()
        outfit = life.outfits.prompt_text()
        state = life.location()
        return NowSchedule(outfit, state.describe())

    async def get_today_schedule(self):
        return TodaySchedule(life=get_life_system())

    async def generate_schedule(self, date: str):
        log.warning(
            '整日日程生成已废弃：现在使用 plan(action="fill", hint=...) 填入式日程'
        )
        return None

    def get_all_schedule(self):
        return {}

    @staticmethod
    def get_now_date():
        return get_now_date()


async def generate_schedule():
    """每日任务调用；不再生成整日日程，只确保生活数据就绪。"""
    log.warning("`generate_schedule()` 已改为 no-op；请用 plan() 填入当天日程")
    try:
        get_life_system().ensure()
    except Exception as exc:
        log.warning(f"生活数据初始化失败：{exc}")


def parse_clock(value):
    """给 WebAPI / 测试用的钟点解析。"""
    return now_time() if value is None else value


__all__ = [
    "ATRISchedule",
    "NowSchedule",
    "ScheduleNode",
    "TodaySchedule",
    "generate_schedule",
    "now",
]
