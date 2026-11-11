from ATRI.bot.status import BotStatus, GlobalStatus
from ATRI.exceptions import str_traceback
from ATRI.log import log

from . import ban
from . import settings as auth_settings


async def migrate_legacy_bans() -> int:
    """把旧 JSON 黑名单迁移成封禁记录，返回新写入的条数。"""
    settings = auth_settings.get_settings()
    if settings.legacy_bans_migrated:
        return 0

    migrated = 0
    for target_type, ids in (
        (ban.TARGET_USER, GlobalStatus.status.user_black_list),
        (ban.TARGET_GROUP, GlobalStatus.status.group_black_list),
    ):
        for target_id in list(ids):
            if not str(target_id):
                continue
            await ban.ban(
                target_type,
                str(target_id),
                reason="旧版全局黑名单迁移",
                source=ban.SOURCE_LEGACY,
                refresh=False,
            )
            migrated += 1

    for bot_id, raw in BotStatus.get_bot_status().items():
        for target_type, key in (
            (ban.TARGET_USER, "user_black_list"),
            (ban.TARGET_GROUP, "group_black_list"),
        ):
            for target_id in raw.get(key, []) or []:
                if not str(target_id):
                    continue
                await ban.ban(
                    target_type,
                    str(target_id),
                    reason=f"旧版机器人 {bot_id} 黑名单迁移",
                    source=ban.SOURCE_LEGACY,
                    refresh=False,
                )
                migrated += 1

    settings.legacy_bans_migrated = True
    auth_settings.save_settings(settings)
    await ban.refresh_cache()
    if migrated:
        log.info(f"已迁移 {migrated} 条旧黑名单记录")
    return migrated


async def init_auth_data() -> None:
    """数据库就绪后加载鉴权数据。

    必须排在 ``init_database()`` 之后（``ATRI.load.load_atri`` 里注册），
    否则表还没建好。失败时只告警：此时封禁查询会退回旧 JSON 名单，
    不影响机器人继续运行。
    """
    try:
        await migrate_legacy_bans()
        await ban.refresh_cache()
        await ban.refresh_group_cache()
        GlobalStatus.sync_from_cache()
        log.info("鉴权数据已加载")
    except Exception as e:
        log.error(f"鉴权数据初始化失败：{str_traceback(e)}")
