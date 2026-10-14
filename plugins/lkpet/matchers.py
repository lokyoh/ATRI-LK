"""Command matchers for the pet plugin.

The plugin's main command is ``宠物``, so every command lives in that namespace
(``/宠物.状态``, ``/宠物.喂食``, …).  Generic words such as ``训练`` / ``休息`` /
``技能`` deliberately do **not** sit at the top level: they would collide with
any future plugin that wants the same word, and nonebot silently drops the
second registration of a duplicated command prefix.

``/帮助`` is intentionally absent: the framework's 帮助 service already owns that
command and will list these matchers for us.
"""

from ATRI.permission import ADMIN, MASTER

from . import plugin

"""The service's main command; every sub-command is registered under it."""

pet_create_matcher = plugin.cmd_as_group("创建", "创建你的专属宠物（每人仅一只）")
pet_status_matcher = plugin.cmd_as_group("状态", "查看宠物的状态与属性")
pet_daily_matcher = plugin.cmd_as_group("结算", "手动触发日结算")
pet_feed_matcher = plugin.cmd_as_group("喂食", "喂食宠物（消耗行动点）")
pet_play_matcher = plugin.cmd_as_group("玩耍", "陪宠物玩耍（消耗行动点）")
pet_pet_matcher = plugin.cmd_as_group("抚摸", "抚摸宠物，增加亲密度")
pet_bathe_matcher = plugin.cmd_as_group("洗澡", "给宠物洗澡（消耗行动点）")
pet_train_matcher = plugin.cmd_as_group("训练", "训练宠物属性（消耗行动点）")
pet_rest_matcher = plugin.cmd_as_group("休息", "让宠物休息，恢复疲劳")
pet_skill_matcher = plugin.cmd_as_group("技能", "查看宠物已学会的技能")
pet_talent_matcher = plugin.cmd_as_group("天赋", "查看或分配天赋点")
pet_bio_matcher = plugin.cmd_as_group("传记", "查看宠物的传记")
pet_log_matcher = plugin.cmd_as_group("日志", "查看最近的每日日志")
pet_reincarnate_matcher = plugin.cmd_as_group(
    "轮回", "查看轮回条件；加「确认」执行轮回"
)
pet_perks_matcher = plugin.cmd_as_group("特权", "查看或兑换成就点特权")
pet_love_matcher = plugin.cmd_as_group("好感度", "查询宠物亲密度")
pet_rename_matcher = plugin.cmd_as_group("改名", "为自己的宠物改名")
pet_instruction_matcher = plugin.cmd_as_group("设定", "更改宠物的设定（聊天人设）")
pet_group_list_matcher = plugin.cmd_as_group(
    "列表", "列出本群所有的宠物", permission=ADMIN
)
pet_all_list_matcher = plugin.cmd_as_group("全部", "列出所有宠物", permission=MASTER)
pet_chat_matcher = plugin.cmd_as_group("聊天", "与宠物聊天")

# ---- 附加玩法（任务书 §12 / §13）：动作作为参数，命令表以 §11 为唯一准 ----

pet_rogue_matcher = plugin.cmd_as_group(
    "肉鸽", "肉鸽爬塔：开始/继续/进度/撤退，局内用编号应答"
)
pet_trpg_matcher = plugin.cmd_as_group(
    "跑团", "跑团（COC 风格 d100）：建卡/卡/重掷/开团/检定/进度/结束"
)
