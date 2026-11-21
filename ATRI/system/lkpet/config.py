from ATRI.configs import Field, PluginConfig, Section


class LKPetConfig(PluginConfig):
    """lkpet设置:

    所有字段均带默认值，用户不改任何一项插件也能正常运行；
    ATRI 会在 data/config/plugins/宠物_config.json 缺失时按默认值落盘并加载。
    游戏数值（属性成长、按天衰减系数、行动点上限等）不在此处，
    仍外置于 res/data/lkpet/config/*.yaml。
    """

    section_basic = Section("basic", "基础", "插件总开关与新手引导")

    enable = Field(bool, True, "养成系统开关")

    tutorial_enabled = Field(bool, True, "启用新手引导")

    section_daily = Section("daily", "日结算", "每天推进状态并推送通知")

    settle_notify = Field(bool, True, "推送日结算通知")

    settle_check_hour = Field(int, 0, "日结算检查小时", min=0, max=23)
    settle_check_minute = Field(int, 0, "日结算检查分钟", min=0, max=59)

    notify_groups = Field(list, [], "日结算通知群", item_type=int)

    section_rogue = Section("rogue", "肉鸽爬塔", "入场券与签到联动")

    rogue_ticket_item = Field(str, "肉鸽入场券", "肉鸽入场券物品名")

    rogue_ticket_per_sign = Field(int, 1, "签到发放入场券", min=0, max=99)

    section_telemetry = Section(
        "telemetry",
        "无尽模式数据收集",
        "记录层数/战斗指令数/选项选择等指标，默认不记录",
    )
    telemetry_enabled = Field(
        bool,
        False,
        "无尽数据收集",
        "记录层推进、战斗指令条数、升级选择、商店动作等指标到 data/plugins/lkpet/telemetry/，"
        "用于评估难度曲线与节奏。默认不记录正文",
    )
    telemetry_include_content = Field(
        bool,
        False,
        "数据收集包含正文",
        "开启后会记录消息正文片段（截断 120 字）。涉及聊天隐私，非排查问题不建议开启",
    )
