from tortoise import fields
from tortoise.models import Model


class BanRecord(Model):
    """一条封禁记录。

    同一个目标可以有多条记录（例如旧版黑名单迁移 + 手动封禁），只要有一条
    处于生效状态就算被封禁。``expires_at = 0`` 表示永久，``revoked_at = 0``
    表示未解封。
    """

    id = fields.BigIntField(pk=True)
    target_type = fields.CharField(max_length=16, index=True)
    target_id = fields.CharField(max_length=100, index=True)
    source = fields.CharField(max_length=16, default="manual")
    reason = fields.TextField(default="")
    operator_id = fields.CharField(max_length=100, default="")
    created_at = fields.FloatField(default=0)
    expires_at = fields.FloatField(default=0)
    revoked_at = fields.FloatField(default=0)

    class Meta:
        table = "auth_ban_record"
        table_description = "鉴权封禁记录表"
        unique_together = (("target_type", "target_id", "source"),)

    @property
    def is_active(self) -> bool:
        return self.revoked_at == 0


class GroupConsole(Model):
    """群控制台。没有记录时视为启用。"""

    group_id = fields.CharField(pk=True, max_length=100)
    enabled = fields.BooleanField(default=True)
    updated_at = fields.FloatField(default=0)

    class Meta:
        table = "auth_group_console"
        table_description = "群控制台表"


class UserLevel(Model):
    """用户在某个群里的权限等级。没有记录时视为 0 级。"""

    id = fields.BigIntField(pk=True)
    group_id = fields.CharField(max_length=100, index=True)
    user_id = fields.CharField(max_length=100, index=True)
    level = fields.IntField(default=0)
    updated_at = fields.FloatField(default=0)

    class Meta:
        table = "auth_user_level"
        table_description = "群内用户等级表"
        unique_together = (("group_id", "user_id"),)
