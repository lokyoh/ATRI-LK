from nonebot.adapters import Bot, Event
from nonebot.exception import IgnoredException
from nonebot.matcher import Matcher
from nonebot.message import event_postprocessor, run_preprocessor
from nonebot.typing import T_State

from ATRI.bot import withdraw
from ATRI.log import log
from ATRI.service import ServiceTools

from . import limit
from . import settings as auth_settings
from . import trace as auth_trace
from .policy import Decision, PolicyResult, evaluate
from .reason import BlockReason
from .snapshot import AuthSnapshot, build_snapshot, event_group_id, event_user_id

log.debug("鉴权流水线已加载：GlobalStatus / BotStatus / 服务配置检查已接管")

_STAGE_LABELS = {
    "global": "GlobalStatus",
    "bot": "BotStatus",
    "service": "Service",
    "malicious": "Malicious",
}

_SEEN_KEY = "atri_auth_seen_services"
_SLOT_KEY = "atri_auth_concurrency_slots"
_PROMPT_SERVICE = "__deny_prompt__"
_PROMPT_FALLBACK = "这个操作现在不可用。"

_DENY_PROMPTS: dict[BlockReason, str] = {
    BlockReason.GLOBAL_DISABLED: "ATRI 现在不想说话...",
    BlockReason.BOT_DISABLED: "这个机器人现在被关掉了。",
    BlockReason.USER_BANNED: "你已被封禁，暂时不能使用 ATRI。",
    BlockReason.USER_NOT_ALLOWED: "你不在允许名单里。",
    BlockReason.GROUP_BANNED: "本群已被封禁。",
    BlockReason.GROUP_NOT_ALLOWED: "本群不在允许名单里。",
    BlockReason.GROUP_DISABLED: "本群已关闭 ATRI。",
    BlockReason.USER_LEVEL_TOO_LOW: "你在这个群的权限等级不够。",
    BlockReason.SERVICE_DISABLED: "这个功能已被关闭。",
    BlockReason.SERVICE_USER_DISABLED: "这个功能对你不可用。",
    BlockReason.SERVICE_GROUP_DISABLED: "这个功能在本群不可用。",
    BlockReason.SERVICE_GROUP_NOT_IN_WHITELIST: "这个功能在本群不可用。",
    BlockReason.SERVICE_BLOCKED_TYPE: "这个功能不支持当前会话。",
    BlockReason.SERVICE_PARENT_DISABLED: "这个功能已被关闭。",
    BlockReason.SERVICE_CD: "太快了，稍后再试。",
    BlockReason.SERVICE_LIMIT: "使用太频繁了，休息一下吧。",
    BlockReason.SERVICE_BUSY: "上一个请求还没处理完。",
    BlockReason.MALICIOUS_BAN: "触发太频繁，已被临时封禁。",
}


def route_gate(event: Event, bot: Bot) -> bool:
    """过滤不需要进入鉴权流水线的事件。

    - 机器人自己发出的消息直接跳过；
    - 既没有用户 id 也没有群 id 的事件没有可判定的主体，跳过。
    """
    user_id = event_user_id(event)
    if user_id and user_id == str(bot.self_id):
        return False
    return bool(user_id or event_group_id(event))


def _record(snapshot: AuthSnapshot, result: PolicyResult) -> None:
    auth_trace.trace_buffer.record(
        decision=result.decision.value,
        stage=result.stage or "pipeline",
        reason=result.reason,
        service=snapshot.matcher_label,
        bot_id=snapshot.bot_id,
        user_id=snapshot.user_id,
        group_id=snapshot.group_id,
    )


def _prompt_for(reason: BlockReason | None) -> str:
    if reason is None:
        return _PROMPT_FALLBACK
    return _DENY_PROMPTS.get(reason, _PROMPT_FALLBACK)


def _ignore_prompt(snapshot: AuthSnapshot) -> bool:
    """服务声明了 ``ignore_prompt`` 时不提示。"""
    service = snapshot.service
    if not service or service not in ServiceTools.service_list:
        return False
    return ServiceTools(service).load_service_config().ignore_prompt


async def _send_deny_prompt(
    bot: Bot,
    event: Event,
    snapshot: AuthSnapshot,
    result: PolicyResult,
    settings: auth_settings.AuthSettings,
) -> None:
    if not settings.deny_prompt or _ignore_prompt(snapshot):
        return
    target = snapshot.group_id or snapshot.user_id
    if not target:
        return
    reason_key = result.reason.value if result.reason is not None else "unknown"
    cooldown = max(0.0, settings.deny_prompt_cooldown)
    if not limit.cooldown.ready(_PROMPT_SERVICE, reason_key, target, cooldown):
        return
    limit.cooldown.touch(_PROMPT_SERVICE, reason_key, target)
    try:
        await bot.send(event, _prompt_for(result.reason))
    except Exception as e:
        log.debug(f"拒绝提示发送失败：{e}")


async def _deny(
    bot: Bot,
    event: Event,
    snapshot: AuthSnapshot,
    result: PolicyResult,
    settings: auth_settings.AuthSettings,
) -> None:
    """统一的拒绝出口：记录追踪、按需提示，然后抛 ``IgnoredException``。"""
    _record(snapshot, result)
    await _send_deny_prompt(bot, event, snapshot, result, settings)
    label = _STAGE_LABELS.get(result.stage, result.stage)
    reason = result.reason.value if result.reason is not None else "unknown"
    raise IgnoredException(
        f"Blocked by {label}: reason={reason}, {snapshot.describe()}"
    )


def _in_malicious_scope(
    settings: auth_settings.AuthSettings, label: str | None
) -> bool:
    if not settings.malicious_enabled or settings.malicious_mode == "off":
        return False
    listed = {str(item) for item in settings.malicious_services if str(item)}
    if settings.malicious_mode == "whitelist":
        return label in listed
    # blacklist：名单里的服务豁免；名单为空表示检测全部
    return label not in listed


async def _check_malicious(
    snapshot: AuthSnapshot, settings: auth_settings.AuthSettings
) -> PolicyResult | None:
    """滑动窗口内触发过多时写临时封禁并拒绝本次事件。"""
    if snapshot.is_master:
        return None
    label = snapshot.service or snapshot.matcher_label
    if not _in_malicious_scope(settings, label):
        return None
    banned = await limit.note_malicious(
        snapshot.user_id,
        window=settings.malicious_window,
        max_count=settings.malicious_max,
        ban_minutes=settings.malicious_ban_minutes,
        reason=(
            f"恶意触发：{settings.malicious_window:g} 秒内超过 "
            f"{settings.malicious_max} 次"
        ),
    )
    if not banned:
        return None
    return PolicyResult(
        Decision.DENY, BlockReason.MALICIOUS_BAN, "malicious", snapshot.describe()
    )


def _apply_withdraw_delay(snapshot: AuthSnapshot, state: T_State) -> None:
    """把服务的 ``withdraw_delay`` 写进 matcher state 与当前上下文。

    NoneBot 的 run-preprocessor 跑在独立子任务里，ContextVar 不会回传给
    handler；真正生效的是写进 ``state`` 的值（handler 运行时由 matcher 读取）。
    ``set_delay`` 只是让直接调用 preprocessor 的场景也能读到同样的值。
    """
    delay = 0.0
    service = snapshot.service
    if service and service in ServiceTools.service_list:
        delay = ServiceTools(service).load_service_config().withdraw_delay
    state[withdraw.STATE_KEY] = delay
    withdraw.set_delay(delay)


def _acquire_slot(snapshot: AuthSnapshot, state: T_State) -> BlockReason | None:
    """为当前服务占用一个并发槽位；同一条事件同一服务只占一个。"""
    service = snapshot.service
    if not service or service not in ServiceTools.service_list:
        return None
    config = ServiceTools(service).load_service_config()
    if config.concurrency <= 0:
        return None

    held: list[tuple[str, str]] = state.setdefault(_SLOT_KEY, [])
    if any(name == service for name, _ in held):
        return None
    if not limit.concurrency.try_acquire(service, snapshot.target, config.concurrency):
        return BlockReason.SERVICE_BUSY
    held.append((service, snapshot.target))
    return None


@run_preprocessor
async def auth_preprocessor(
    event: Event, bot: Bot, matcher: Matcher, state: T_State
) -> None:
    """在事件进入 handler 前执行鉴权流水线。"""
    config = auth_settings.get_settings()
    if not config.enabled:
        return

    auth_trace.trace_buffer.configure(config.trace_buffer)

    if not route_gate(event, bot):
        return

    # 默认不撤回；只有放行后才按服务配置覆盖
    state[withdraw.STATE_KEY] = 0.0
    withdraw.set_delay(0.0)

    snapshot = build_snapshot(event, bot, matcher)

    seen: set[str] = state.setdefault(_SEEN_KEY, set())
    service = snapshot.service
    consume = bool(service) and service not in seen

    malicious = await _check_malicious(snapshot, config)
    if malicious is not None:
        await _deny(bot, event, snapshot, malicious, config)

    result = evaluate(snapshot, consume=consume)
    if result.denied:
        await _deny(bot, event, snapshot, result, config)

    if consume and service:
        seen.add(service)

    busy = _acquire_slot(snapshot, state)
    if busy is not None:
        result = PolicyResult(Decision.DENY, busy, "service", snapshot.describe())
        await _deny(bot, event, snapshot, result, config)

    _apply_withdraw_delay(snapshot, state)
    _record(snapshot, result)


@event_postprocessor
async def auth_postprocessor(state: T_State) -> None:
    """事件处理结束后释放并发槽位。"""
    for service, target in state.pop(_SLOT_KEY, []):
        limit.concurrency.release(service, target)
