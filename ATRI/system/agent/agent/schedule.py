import datetime
import json

from ATRI.dir import SYS_CONFIG_DIR
from ATRI.event import daily_update
from ATRI.log import log

from ..llm import ModelType, llm_manager
from .atri import ATRI


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
        now_time = datetime.datetime.now().time()
        if 2 <= now_time.hour < 6:
            return "睡眠中。"
        for s in self.schedule:
            start = s.start_time
            end = s.end_time
            if start < end:
                if start <= now_time < end:
                    return s.schedule
            else:
                if now_time >= start or now_time < end:
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
            with open(self.data_file, "r", encoding="utf-8") as f:
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
        try:
            his_sch = (
                "\n".join(
                    f"{s}:{self.schedule_data[s].get('daily_schedule', '错误日程')}"
                    for s in self.schedule_data
                )
                if self.schedule_data
                else "暂无日程"
            )
            prompt = (
                self.before_prompt
                + f"\n\n### 人物简介\n{ATRI.get_role_prompt()}\n\n### 历史日程\n{his_sch}\n\n"
                + self.after_prompt
            )
            schedule = await llm_manager.call_model_by_type(ModelType.TOOL, prompt)
            s_data = json.loads(schedule.get("content"))
        except Exception as e:
            raise e
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
        today = self.get_now_date()
        if today not in self.schedule_data:
            await self.generate_schedule(today)
        try:
            return TodaySchedule(self.schedule_data[today])
        except Exception:
            del self.schedule_data[today]
            log.warning("日程生成不符合规范，已删除，如果多次遇到请考虑更换模型。")
            return None

    def get_all_schedule(self):
        """获取所有的日程"""
        return self.schedule_data

    @staticmethod
    def get_now_date():
        if datetime.datetime.now().hour >= 6:
            return datetime.date.today().strftime("%Y-%m-%d")
        else:
            return (datetime.date.today() - datetime.timedelta(days=1)).strftime(
                "%Y-%m-%d"
            )


@daily_update()
async def generate_schedule():
    log.info("开始更新亚托莉日程...")
    await ATRISchedule().generate_schedule(datetime.date.today().strftime("%Y-%m-%d"))
