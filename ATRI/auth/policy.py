from dataclasses import dataclass
from enum import Enum

from ATRI.bot.status import BotStatus, GlobalStatus
from ATRI.service import ServiceConfig, ServiceTools

from . import limit
from .reason import BlockReason
from .snapshot import AuthSnapshot


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True)
class PolicyResult:
    decision: Decision
    reason: BlockReason | None = None
    stage: str = ""
    detail: str = ""

    @property
    def denied(self) -> bool:
        return self.decision is Decision.DENY


ALLOW = PolicyResult(Decision.ALLOW)


def check_global(snapshot: AuthSnapshot) -> PolicyResult:
    reason = GlobalStatus.check_blocked(
        snapshot.user_id or None, snapshot.group_id or None
    )
    if reason is None:
        return ALLOW
    return PolicyResult(Decision.DENY, reason, "global", snapshot.describe())


def check_bot(snapshot: AuthSnapshot) -> PolicyResult:
    reason = BotStatus.check_blocked(
        snapshot.bot_id, snapshot.user_id or None, snapshot.group_id or None
    )
    if reason is None:
        return ALLOW
    return PolicyResult(Decision.DENY, reason, "bot", snapshot.describe())


def check_service(snapshot: AuthSnapshot) -> PolicyResult:
    service = snapshot.service
    if not service or service not in ServiceTools.service_list:
        return ALLOW
    reason = ServiceTools(service).check_service(
        snapshot.user_id or None, snapshot.group_id or None
    )
    if reason is None:
        return ALLOW
    return PolicyResult(Decision.DENY, reason, "service", snapshot.describe())


def _targets(snapshot: AuthSnapshot, scope: str) -> list[tuple[str, str]]:
    """按作用范围展开成 ``(scope, target)`` 列表。"""
    pairs = (("user", snapshot.user_id), ("group", snapshot.group_id))
    if scope == "user":
        pairs = (("user", snapshot.user_id),)
    elif scope == "group":
        pairs = (("group", snapshot.group_id),)
    return [(name, target) for name, target in pairs if target]


def _runtime_reason(
    service: str, config: ServiceConfig, snapshot: AuthSnapshot, current: float
) -> BlockReason | None:
    targets = _targets(snapshot, config.limit_scope)
    if not targets:
        return None

    if config.cd > 0:
        for scope, target in targets:
            if not limit.cooldown.ready(
                service, scope, target, config.cd, current=current
            ):
                return BlockReason.SERVICE_CD

    if config.max_count > 0 and config.limit_window > 0:
        for scope, target in targets:
            if not limit.counter.can_hit(
                service,
                scope,
                target,
                config.limit_window,
                config.max_count,
                current=current,
            ):
                return BlockReason.SERVICE_LIMIT
    return None


def _consume(
    service: str, config: ServiceConfig, snapshot: AuthSnapshot, current: float
) -> None:
    for scope, target in _targets(snapshot, config.limit_scope):
        if config.cd > 0:
            limit.cooldown.touch(service, scope, target, current=current)
        if config.max_count > 0 and config.limit_window > 0:
            limit.counter.hit(service, scope, target, current=current)


def check_runtime(snapshot: AuthSnapshot, *, consume: bool = True) -> PolicyResult:
    """检查服务的冷却与计次限制；``consume=False`` 时直接放行。

    同一条事件命中同一服务的多个 matcher 时，只有第一次需要判定 / 计次。
    """
    service = snapshot.service
    if not consume or not service or service not in ServiceTools.service_list:
        return ALLOW
    config = ServiceTools(service).load_service_config()
    current = limit.now()
    reason = _runtime_reason(service, config, snapshot, current)
    if reason is not None:
        return PolicyResult(Decision.DENY, reason, "service", snapshot.describe())
    _consume(service, config, snapshot, current)
    return ALLOW


CHECKS = (check_global, check_bot, check_service)


def evaluate(snapshot: AuthSnapshot, *, consume: bool = True) -> PolicyResult:
    """按固定顺序判定，返回第一个拦截原因。"""
    for check in CHECKS:
        result = check(snapshot)
        if result.denied:
            return result
    return check_runtime(snapshot, consume=consume)
