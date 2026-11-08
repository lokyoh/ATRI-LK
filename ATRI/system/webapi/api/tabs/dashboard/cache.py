"""机器人身份信息的缓存。

## 为什么要缓存

WebUI 仪表盘每次刷新都会调用 ``/dashboard/get_bot_list``，而构建一条 BotInfo
要打 three 次远端：

- QQ 官方：``bot.me()`` —— 走 QQ 开放平台，**最慢**，网络抖一下就是几秒；
- OneBot：``get_login_info()``；
- 两边都要：``get_group_list()`` + ``get_friend_list()``（QQ 端直接返回空）。

这些值（昵称、头像、群数、好友数）都是**几乎不变**的身份信息，一天里反复拉
没有意义。慢的恰恰是它们，统计数字反而是本地数据库查询。

## 缓存什么、不缓存什么

只缓存**远端身份信息**（``Identity``）。今日消息数、今日调用数、连接时间这些
必须每次实时算——它们就是这个页面要展示的东西，缓存了会看到过期数据。

## 为什么不缓存整条 BotInfo

那会把「今日消息数」也冻住：用户刷新页面期待的是最新数字。分开缓存，才能做到
「慢的部分只拉一次，快的部分每次都新」。

## 缓存失效

- **TTL**：默认 10 分钟，够覆盖一个人的反复刷新，又不至于让改了昵称迟迟不生效；
- **手动清空**：``clear()``，Bot 重连时调用（重连意味着 ``self_id`` 对应的账号
  信息可能变了）；
- **按 self_id 分键**：多机器人部署互不干扰。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from ATRI.log import log

#: 身份信息缓存多久（秒）。手动改昵称/头像后最多这么久生效。
IDENTITY_TTL = 600.0


def _now() -> float:
    """当前时刻（单调时钟）。

    单独包一层是为了测试能把时间「推快」——否则验过期要真等十分钟。
    """
    return time.monotonic()


@dataclass
class Identity:
    """一台机器人的远端身份信息——这些值不应该每次请求都去问平台。"""

    self_id: str
    nickname: str
    ava_url: str
    platform: str
    group_count: int = 0
    friend_count: int = 0


@dataclass
class _Entry:
    identity: Identity
    at: float
    ttl: float


_entries: dict[str, _Entry] = {}
"""``self_id -> 缓存条目``。模块级单例，进程生命周期内有效。"""


def get(self_id: str, *, ttl: float | None = None) -> Identity | None:
    """取缓存；过期或没有就返回 None。

    :param ttl: 不传时沿用**写入时**记录的 TTL。早期实现读的时候重新取默认值，
        于是「按短 TTL 写入、按默认 TTL 读取」的条目永远不会过期。
    """
    entry = _entries.get(str(self_id))
    if entry is None:
        return None
    lifetime = entry.ttl if ttl is None else ttl
    if lifetime <= 0 or (_now() - entry.at) >= lifetime:
        _entries.pop(str(self_id), None)
        return None
    return entry.identity


def put(identity: Identity, *, ttl: float | None = None) -> None:
    """写入缓存；``ttl <= 0`` 表示不缓存。"""
    lifetime = IDENTITY_TTL if ttl is None else ttl
    if lifetime <= 0:
        return
    _entries[str(identity.self_id)] = _Entry(identity=identity, at=_now(), ttl=lifetime)
    log.debug(f"机器人 {identity.self_id} 身份信息已缓存（{lifetime:.0f}s）")


def clear(self_id: str | None = None) -> None:
    """清空缓存。传 ``self_id`` 只清那一台。"""
    if self_id is None:
        _entries.clear()
        return
    _entries.pop(str(self_id), None)


def stats() -> dict[str, float | int]:
    """当前缓存状态，供排障/测试观察。"""
    now = _now()
    return {
        "size": len(_entries),
        "ttl": IDENTITY_TTL,
        "oldest_age": max((now - e.at for e in _entries.values()), default=0.0),
    }
