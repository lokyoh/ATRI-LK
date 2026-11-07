import datetime
import json
import re

from ATRI.dir import SYS_CONFIG_DIR
from ATRI.log import log
from ATRI.utils.datetime import now, now_time, today

from ..llm import ModelType, llm_manager
from .role import ATRI

#: 「昨日记忆」注入日程生成的字数预算。日程只需要气质与近况，不需要全部记忆。
SCHEDULE_MEMORY_BUDGET = 600

#: 「她认识的人」注入日程生成的字数预算。
#: 日程是给「她自己的一天」用的，只需要知道有谁值得出现在这一天里。
SCHEDULE_PEOPLE_BUDGET = 400


def _describe_people() -> str | None:
    """把认识的几个人整理成日程生成的参考素材。

    为什么需要：原来的日程只参考「昨日记忆」，于是生成的永远是
    「和朋友约在咖啡馆见面」这种泛称——她明明有具体的主人、常聊的人，
    日程里却一个都不出现，整天像是悬浮在没人际关系的真空里。

    排序按**熟络度**，主人优先。只给昵称与关系，不给 QQ 号：
    日程是叙事文本，塞满数字会让她把 ID 念出来。

    :return: 可直接拼进提示词的段落；没人可写时返回 None。
    """
    from .user import list_known_users

    try:
        users = list_known_users()
    except Exception:
        return None
    if not users:
        return None

    from .util import get_user_group

    master_id = ""
    lines: list[str] = []
    used = 0
    unknown_seq = 0
    for u in users:
        uid = u["user_id"]
        try:
            is_master = get_user_group(uid) == "主人"
        except Exception:
            is_master = False
        if is_master and not master_id:
            master_id = uid
        try:
            from ATRI.system.lkapi.bot import util as lk_util

            label = lk_util.get_name(uid) if lk_util.is_valid_user(uid) else ""
        except Exception:
            label = ""
        if not label:
            if is_master:
                label = "主人"
            else:
                unknown_seq += 1
                label = f"某位熟人{unknown_seq}"
        role = "主人" if is_master else "熟人"
        detail = f"- {label}（{role}）：{u['relation']}"
        if u["profile"]:
            detail += f"；你对TA的印象：{u['profile']}"
        if used + len(detail) > SCHEDULE_PEOPLE_BUDGET:
            break
        lines.append(detail)
        used += len(detail)
    if not lines:
        return None
    head = "### 她生活里真实存在的人\n"
    if master_id:
        head += "主人是她最重要的人，日程里应当自然出现，而不是用「朋友」泛指。\n"
    head += "生成日程时请围绕这些人展开（可以一起吃饭、聊天、被叫走），"
    head += "不要凭空造一群泛泛的「朋友」。\n"
    return head + "\n".join(lines) + "\n\n"


def extract_json_from_markdown(text):
    """
    从带 ```json``` 的文本中提取并解析 JSON
    自动处理：尾随逗号、换行、语言标识
    """
    # 1. 提取代码块
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    json_str = match.group(1) if match else text.strip()
    # 2. 先尝试标准解析
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass
    # 3. 兜底：清洗尾随逗号后再试
    cleaned = re.sub(r",\s*([}\]])", r"\1", json_str)
    return json.loads(cleaned)


class NowSchedule:
    def __init__(self, today_outfit: str = "Null", now_schedule: str = "Null"):
        self.outfit: str = today_outfit
        self.now_schedule: str = now_schedule


class ScheduleNode:
    def __init__(self, data):
        self.start_time = datetime.time.fromisoformat(data["start_time"])
        self.end_time = datetime.time.fromisoformat(data["end_time"])
        self.schedule: str = data["schedule"]


class TodaySchedule:
    def __init__(self, data):
        self.outfit: str = data["outfit"]
        self.schedule: list[ScheduleNode] = []
        for s in data["schedule"]:
            self.schedule.append(ScheduleNode(s))

    def get_schedule_from_now(self):
        _now_time = now_time()
        if 2 <= _now_time.hour < 6:
            return "睡眠中。"
        for s in self.schedule:
            start = s.start_time
            end = s.end_time
            if start < end:
                if start <= _now_time < end:
                    return s.schedule
            else:
                if _now_time >= start or _now_time < end:
                    return s.schedule
        return None


class ATRISchedule:
    """亚托莉的日程表"""

    before_prompt = """请生成亚托莉物在虚构世界中的一日详细日程，时间跨度为当日 **06:00** 至次日 **02:00**。
你需要严格按照以下要求输出结果：
1. **输出格式**：必须为纯 JSON，不包含任何额外的解释、说明或 Markdown 代码块标记。
2. JSON 中需包含两个字段：
   - `outfit`：字符串，详细描述该人物今日的完整穿搭，包括服装、配饰、鞋履、妆容、发型等。
   - `schedule`：日程列表，以清晰的时间线形式详细记录从 06:00 至次日 02:00 的每一个行程安排，并附带简要但生动的活动描述。
3. 日程应真实、具体且富有生活感，并体现该人物的身份、职业、性格或故事背景。应至少包含 10 个不同的时间节点。
4. 人物身份与日程可自由发挥，但必须建立在连贯、合理的情节逻辑之上。
5. 日程可以参考历史日程但不要历史日程一至。"""
    after_prompt = """"### 输出示例
```json
{
  "outfit": "...",
  "schedule": [
    {
      "start_time": "06:00",
      "end_time": "07:00",
      "schedule": "...",
    },
    ...
  ]
}
```

请开始生成。"""

    def __init__(self):
        self.schedule_data = {}
        self.data_file = SYS_CONFIG_DIR / "ATRI_schedule.json"
        self._load_data()

    def _load_data(self):
        """加载日程数据"""
        if self.data_file.exists():
            with open(self.data_file, encoding="utf-8") as f:
                self.schedule_data = json.load(f)
            keys = list(self.schedule_data.keys())
            for s in keys:
                try:
                    TodaySchedule(self.schedule_data[s])
                except Exception:
                    del self.schedule_data[s]
                    log.debug(f"删除不可用日程:{s}")

    def _save_data(self):
        """保存日程数据"""
        with open(self.data_file, "w", encoding="utf-8") as f:
            json.dump(self.schedule_data, f, ensure_ascii=False, indent=4)

    async def generate_schedule(self, date: str):
        """生成最新的日程"""
        from .memory.manage import memory_manager

        memory_date = (
            datetime.date.fromisoformat(date) - datetime.timedelta(days=1)
        ).isoformat()
        memory = await memory_manager.get_memories_by_date(memory_date)
        if memory:
            ranked = sorted(
                memory, key=lambda m: m.get("importance", 0.0), reverse=True
            )
            lines: list[str] = []
            used = 0
            for item in ranked:
                text = str(item.get("content", "")).strip()
                if not text:
                    continue
                if used + len(text) > SCHEDULE_MEMORY_BUDGET:
                    break
                lines.append(text)
                used += len(text)
            memory = "\n".join(lines) if lines else None
        else:
            memory = None
        prompt = (
            self.before_prompt
            + f"\n\n### 人物简介\n{ATRI.get_role_prompt()}\n\n"
            + (_describe_people() or "")
            + f"{f'### 昨日记忆\n{memory}\n\n' if memory else ''}"
            + self.after_prompt
        )
        try:
            schedule = await llm_manager.call_model_by_type(
                ModelType.TOOL, prompt, "Agent-ATRI日程生成"
            )
            s_data = extract_json_from_markdown(schedule.content)
        except Exception as e:
            log.error(f"日程生成失败：{e}")
            return
        self.schedule_data[date] = s_data
        self.schedule_data = {
            k: self.schedule_data[k] for k in sorted(self.schedule_data.keys())[-5:]
        }
        self._save_data()

    async def get_schedule(self):
        """获取当前的日程"""
        today_schedule = await self.get_today_schedule()
        if today_schedule is None:
            return NowSchedule()
        now_schedule = today_schedule.get_schedule_from_now()
        if now_schedule is None:
            now_schedule = "Null"
        return NowSchedule(today_schedule.outfit, now_schedule)

    async def get_today_schedule(self):
        """获取今天的日程"""
        _today = self.get_now_date()
        if _today not in self.schedule_data:
            await self.generate_schedule(_today)
        try:
            return TodaySchedule(self.schedule_data[_today])
        except Exception:
            self.schedule_data.pop(_today, None)
            log.warning("日程生成不符合规范，已删除，如果多次遇到请考虑更换模型。")
            return None

    def get_all_schedule(self):
        """获取所有的日程"""
        return self.schedule_data

    @staticmethod
    def get_now_date():
        if now().hour >= 6:
            return today().strftime("%Y-%m-%d")
        else:
            return (today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")


async def generate_schedule():
    log.info("开始更新亚托莉日程...")
    await ATRISchedule().generate_schedule(today().strftime("%Y-%m-%d"))
