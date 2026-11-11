import time
from collections.abc import Iterable

from ATRI.log import log

from .model import BanRecord, GroupConsole, UserLevel

TARGET_USER = "user"
TARGET_GROUP = "group"

SOURCE_MANUAL = "manual"
SOURCE_LEGACY = "legacy"
SOURCE_MALICIOUS = "malicious"


def _now() -> float:
    """当前时间（秒）。单独抽出来便于测试替换。"""
    return time.time()


class BanCache:
    """生效中的封禁快照。

    ``_active`` 的 value 是生效结束时间，``0`` 表示永久。同一目标有多条
    记录时取最晚失效；任意一条永久则整体永久。
    """

    def __init__(self) -> None:
        self._active: dict[tuple[str, str], float] = {}
        self.loaded = False

    def is_banned(
        self, target_type: str, target_id: str, *, now: float | None = None
    ) -> bool:
        if not target_id:
            return False
        expires = self._active.get((target_type, target_id))
        if expires is None:
            return False
        return not expires or expires > (_now() if now is None else now)

    def expires_at(
        self, target_type: str, target_id: str, *, now: float | None = None
    ) -> float | None:
        """生效结束时间；未封禁返回 ``None``，永久返回 ``0``。"""
        if not self.is_banned(target_type, target_id, now=now):
            return None
        return self._active[(target_type, target_id)]

    def active_ids(self, target_type: str, *, now: float | None = None) -> list[str]:
        """当前生效的目标 id，按字符串排序。"""
        current = _now() if now is None else now
        return sorted(
            target_id
            for (kind, target_id), expires in self._active.items()
            if kind == target_type and (not expires or expires > current)
        )

    def replace(self, active: dict[tuple[str, str], float]) -> None:
        self._active = dict(active)
        self.loaded = True

    def clear(self) -> None:
        self._active.clear()
        self.loaded = False


class GroupCache:
    """群开关与群内用户等级快照。"""

    def __init__(self) -> None:
        self._disabled: set[str] = set()
        self._levels: dict[tuple[str, str], int] = {}
        self.loaded = False

    def is_enabled(self, group_id: str) -> bool:
        return group_id not in self._disabled

    def level_of(self, group_id: str, user_id: str) -> int:
        return self._levels.get((group_id, user_id), 0)

    def levels_in(self, group_id: str) -> dict[str, int]:
        return {
            user_id: level
            for (gid, user_id), level in self._levels.items()
            if gid == group_id
        }

    def replace(
        self, disabled: Iterable[str], levels: dict[tuple[str, str], int]
    ) -> None:
        self._disabled = set(disabled)
        self._levels = dict(levels)
        self.loaded = True

    def clear(self) -> None:
        self._disabled.clear()
        self._levels.clear()
        self.loaded = False


ban_cache = BanCache()
group_cache = GroupCache()


def is_banned(target_type: str, target_id: str, legacy: Iterable[str] = ()) -> bool:
    """同步查询封禁状态。

    :param legacy: 旧 JSON 名单。数据库还没加载完时作为兜底，避免启动瞬间
        出现“黑名单失效”的空窗。
    """
    if ban_cache.is_banned(target_type, target_id):
        return True
    return not ban_cache.loaded and target_id in legacy


async def refresh_cache() -> None:
    """从数据库重建封禁快照。"""
    current = _now()
    active: dict[tuple[str, str], float] = {}
    for row in await BanRecord.filter(revoked_at=0).all():
        expires = float(row.expires_at or 0)
        if expires and expires <= current:
            continue
        key = (row.target_type, row.target_id)
        if key not in active:
            active[key] = expires
            continue
        previous = active[key]
        if previous == 0:
            continue
        active[key] = 0.0 if expires == 0 else max(previous, expires)
    ban_cache.replace(active)


async def refresh_group_cache() -> None:
    """从数据库重建群开关与用户等级快照。"""
    disabled = {row.group_id for row in await GroupConsole.all() if not row.enabled}
    levels = {(row.group_id, row.user_id): row.level for row in await UserLevel.all()}
    group_cache.replace(disabled, levels)


async def ban(
    target_type: str,
    target_id: str,
    *,
    minutes: float = 0,
    reason: str = "",
    operator_id: str = "",
    source: str = SOURCE_MANUAL,
    refresh: bool = True,
) -> BanRecord:
    """写入一条封禁记录。

    :param minutes: 时长（分钟），``<= 0`` 表示永久
    :param refresh: 批量写入时可传 ``False``，最后统一刷新快照
    """
    target_id = str(target_id)
    now = _now()
    expires_at = now + minutes * 60 if minutes > 0 else 0.0
    record, _ = await BanRecord.get_or_create(
        target_type=target_type,
        target_id=target_id,
        source=source,
    )
    record.reason = reason
    record.operator_id = str(operator_id or "")
    record.created_at = now
    record.expires_at = expires_at
    record.revoked_at = 0.0
    await record.save()
    if refresh:
        await refresh_cache()
    return record


async def unban(target_type: str, target_id: str, *, refresh: bool = True) -> int:
    """解封目标，返回被撤销的记录数。"""
    target_id = str(target_id)
    now = _now()
    rows = await BanRecord.filter(
        target_type=target_type, target_id=target_id, revoked_at=0
    ).all()
    for row in rows:
        row.revoked_at = now
        await row.save(update_fields=["revoked_at"])
    if refresh:
        await refresh_cache()
    return len(rows)


async def list_bans(
    target_type: str | None = None, *, include_inactive: bool = False
) -> list[BanRecord]:
    """列出封禁记录（按创建时间倒序）。"""
    query = BanRecord.all()
    if target_type:
        query = query.filter(target_type=target_type)
    if not include_inactive:
        query = query.filter(revoked_at=0)
    return await query.order_by("-created_at").all()


async def purge_expired(*, now: float | None = None) -> int:
    """删除已经过期的封禁记录，返回删除条数。"""
    current = _now() if now is None else now
    count = await BanRecord.filter(expires_at__gt=0, expires_at__lte=current).delete()
    if count:
        await refresh_cache()
        log.debug(f"已清理 {count} 条过期封禁记录")
    return int(count)


async def apply_blacklists(
    user_ids: Iterable[str],
    group_ids: Iterable[str],
    *,
    operator_id: str = "",
    reason: str = "WebUI 设置",
) -> None:
    """按 WebUI 提交的名单增删封禁记录。

    这是“整份名单”语义：提交里没有的目标会被解封。已生效的目标保持不变，
    不会刷新封禁时长。
    """
    for target_type, ids in (
        (TARGET_USER, user_ids),
        (TARGET_GROUP, group_ids),
    ):
        wanted = {str(i) for i in ids if str(i)}
        current = set(ban_cache.active_ids(target_type))
        for target_id in sorted(wanted - current):
            await ban(
                target_type,
                target_id,
                reason=reason,
                operator_id=operator_id,
                refresh=False,
            )
        for target_id in sorted(current - wanted):
            await unban(target_type, target_id, refresh=False)
    await refresh_cache()


async def set_group_enabled(group_id: str, enabled: bool) -> None:
    """设置群开关并刷新快照。"""
    group_id = str(group_id)
    row, _ = await GroupConsole.get_or_create(group_id=group_id)
    row.enabled = bool(enabled)
    row.updated_at = _now()
    await row.save()
    await refresh_group_cache()


async def toggle_group_enabled(group_id: str) -> bool:
    """翻转群开关，返回新状态。"""
    group_id = str(group_id)
    enabled = not group_cache.is_enabled(group_id)
    await set_group_enabled(group_id, enabled)
    return enabled


async def set_user_level(group_id: str, user_id: str, level: int) -> None:
    """设置群内用户等级并刷新快照。"""
    group_id = str(group_id)
    user_id = str(user_id)
    row, _ = await UserLevel.get_or_create(group_id=group_id, user_id=user_id)
    row.level = max(0, int(level))
    row.updated_at = _now()
    await row.save()
    await refresh_group_cache()


async def get_user_level(group_id: str, user_id: str) -> int:
    return group_cache.level_of(str(group_id), str(user_id))
