from ATRI.configs import Field, PluginConfig


class LKPetConfig(PluginConfig):
    """
    lkpet设置:

    所有字段均带默认值，用户不改任何一项插件也能正常运行；
    ATRI 会在 data/config/plugins/宠物_config.json 缺失时按默认值落盘并加载。
    游戏数值（属性成长、按天衰减系数、行动点上限等）不在此处，
    仍外置于 res/data/lkpet/config/*.yaml。
    """

    # 养成系统总开关；关闭后养成指令不再响应（Step 12 指令处理器）
    enable = Field(bool, True, "养成系统开关")

    # 日结算后是否推送「新的一天」通知（Step 12）
    settle_notify = Field(bool, True, "推送日结算通知")

    # 每日检查日结算的时间，服务器时区（Step 12 定时任务）
    settle_check_hour = Field(int, 0, "日结算检查小时", min=0, max=23)
    settle_check_minute = Field(int, 0, "日结算检查分钟", min=0, max=59)

    # 接收日结算通知的群号；留空表示不主动推送，仅在玩家查询时返回
    notify_groups = Field(list, [], "日结算通知群", item_type=int)

    # 是否启用按天推进的新手引导（Step 14）
    tutorial_enabled = Field(bool, True, "启用新手引导")

    # 肉鸽入场券：LK 背包物品名（签到发放、入场时自动消耗）
    rogue_ticket_item = Field(str, "肉鸽入场券", "肉鸽入场券物品名")

    # 每次签到发放的入场券张数；0 = 关闭签到发券（仍可扣行动点入场）
    rogue_ticket_per_sign = Field(int, 1, "签到发放入场券", min=0, max=99)
