from tortoise import fields
from tortoise.models import Model


class SchedulerTask(Model):
    """持久化的定时任务定义。"""

    # 唯一键：`<service>:<name>`。与 SchedulerController 的命名空间一致
    key = fields.CharField(pk=True, max_length=200)
    # 所属服务（SchedulerController.service）
    service = fields.CharField(max_length=100, index=True, default="")
    # 服务内的任务名
    name = fields.CharField(max_length=200, default="")
    # 触发器类型：date / cron / interval
    trigger = fields.CharField(max_length=32, default="cron")
    # 触发器参数，JSON。date 存 {"run_date": "..."}，cron 存 {"hour": 8, ...}
    trigger_args = fields.TextField(default="{}")
    # 传给任务函数的 kwargs，JSON。只有能 JSON 化的参数才允许持久化
    job_kwargs = fields.TextField(default="{}")
    # 创建时间（UTC 秒）
    created_at = fields.FloatField(default=0)

    class Meta:
        table = "scheduler_task"
        table_description = "核心定时任务持久化表"
