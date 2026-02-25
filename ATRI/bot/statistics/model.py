from tortoise import fields
from tortoise.models import Model


class MessageStatistics(Model):
    """
    消息统计模型类，对应数据库中的一张表
    """

    # 主键，自增ID
    id = fields.BigIntField(pk=True)
    # botID
    bot_id = fields.CharField(max_length=100, index=True)
    # 发送者ID
    user_id = fields.CharField(max_length=100, index=True)
    # 群组ID，如果是私聊消息则为空
    group_id = fields.CharField(max_length=100, null=True, index=True)
    # 消息类型，如 "private" 或 "group"
    message_type = fields.CharField(max_length=20)
    # 消息内容简略
    content = fields.TextField()
    # 消息接收时间，自动设置为创建时间
    created_at = fields.DatetimeField(auto_now_add=True)

    # 定义表名
    class Meta:
        table = "message_statistics"
        table_description = "消息统计表"


class ServiceStatistics(Model):
    """
    服务调用统计模型类，对应数据库中的一张表
    """

    # 主键，自增ID
    id = fields.BigIntField(pk=True)
    # botID
    bot_id = fields.CharField(max_length=100, index=True)
    # 服务ID
    service_id = fields.CharField(max_length=100, index=True)
    # 调用类型
    call_type = fields.CharField(max_length=20, index=True)
    # 目标ID
    target_id = fields.CharField(max_length=100, index=True)
    # 消息类型，如 "private" 或 "group"
    target_type = fields.CharField(max_length=20)
    # 错误追踪ID
    track_id = fields.CharField(max_length=8, null=True)
    # 调用时间，自动设置为创建时间
    created_at = fields.DatetimeField(auto_now_add=True)

    # 定义表名
    class Meta:
        table = "service_statistics"
        table_description = "服务调用统计表"
