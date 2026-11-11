from ATRI.configs import Field, PluginConfig


class BackupConfig(PluginConfig):
    """备份设置。"""

    enabled = Field(
        bool,
        True,
        "启用自动备份",
        "按设定的时间每天自动打包一次",
    )
    cron_hour = Field(int, 4, "自动备份小时", min=0, max=23)
    cron_minute = Field(int, 30, "自动备份分钟", min=0, max=59)
    directories = Field(
        list,
        ["data"],
        "备份目录",
        "相对项目根目录；data/sql、data/temp 与备份目录自身会被排除",
        item_type=str,
    )
    keep = Field(
        int,
        7,
        "保留份数",
        "超出后删除最旧的备份；0 表示不轮转",
        min=0,
    )
    target_dir = Field(
        str,
        "data/backups",
        "备份存放目录",
        "相对项目根目录；不会被打进备份",
    )
    include_db = Field(
        bool,
        True,
        "包含数据库",
        "对 data/sql 下的 SQLite 做一致性快照",
    )
