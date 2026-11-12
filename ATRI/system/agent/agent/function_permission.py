"""三级权限与功能作用域求值。

权限模型只有三个账户等级，另有一个只由代码构造的内部伪角色 ``system``：

- ``master``：主人，能力上限 ``bot``，可操作 bot 全局；
- ``admin``：当前群的群主 / 群管理，能力上限 ``group``；
- ``user``：其他所有人，能力上限 ``chat``；
- ``system``：主动开口 / 无委托人的内部链路，能力上限 ``bot``，不对外开放。

权限只用来守住**框架边界**：配置、文件、服务与命令查询、跨群操作等
``bot`` 作用域能力。亚托莉自己的情绪、好感、关系、记忆、日程、着装等
生活功能属于 ``chat`` 作用域，普通用户也能触发——用户和她聊天本来就会
影响她的生活，不需要额外的权限。

**默认权限由功能自己声明**：``scope`` / ``scope_by_action`` 就是默认值。
``permission`` 与 ``permission_users`` 只记录**覆盖项**，留空表示不覆盖。

求值顺序固定，并且每一步都记录原因，供日志与 WebUI 的 explain 还原：

1. 功能不存在
2. essential（核心闭环，永不禁用）
3. 全局硬禁用
4. 功能插件整体禁用
5. 解析账户等级（含个人 ``downgrade_to``）
6. 解析功能作用域（``scope_by_action`` 优先）
7. 能力上限检查（硬边界，任何 allow 都不能越过）
8. 个人 deny
9. 个人 allow
10. 等级 deny
11. 等级 allow
12. 功能默认作用域允许
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from fnmatch import fnmatchcase
from typing import Any

from ATRI.log import log
from ATRI.permission import is_master_user, resolve_account_id

from ..permission_defaults import (
    DEFAULT_USER_PERMISSION_ITEM,
    LEVEL_LABELS,
)
from ..utils import log as agent_log


class PermissionLevel(StrEnum):
    MASTER = "master"
    ADMIN = "admin"
    USER = "user"
    SYSTEM = "system"


class Capability(StrEnum):
    CHAT = "chat"
    GROUP = "group"
    BOT = "bot"


class FunctionScope(StrEnum):
    CHAT = "chat"
    GROUP = "group"
    BOT = "bot"


#: 等级到能力上限的映射是**代码常量**，不开放配置。
#: 一旦允许把 user 的上限改成 bot，「三级权限」就名存实亡。
LEVEL_CAPABILITY: dict[PermissionLevel, Capability] = {
    PermissionLevel.MASTER: Capability.BOT,
    PermissionLevel.ADMIN: Capability.GROUP,
    PermissionLevel.USER: Capability.CHAT,
    PermissionLevel.SYSTEM: Capability.BOT,
}

CAPABILITY_RANK: dict[Capability, int] = {
    Capability.CHAT: 1,
    Capability.GROUP: 2,
    Capability.BOT: 3,
}

#: 是否记录拒绝埋点。WebUI 的 explain / 批量预览只做计算，不应污染 telemetry。
_RECORD_DENIALS: ContextVar[bool] = ContextVar(
    "permission_record_denials", default=True
)


@dataclass(frozen=True)
class LevelOverride:
    """一个等级的**覆盖**策略。等级本身固定，默认权限由功能自己声明。"""

    level: PermissionLevel
    label: str
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()


@dataclass(frozen=True)
class UserOverride:
    """个人覆盖：只能在上限内收紧或放宽，不能提权。"""

    user_id: str
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    downgrade_to: PermissionLevel | None = None


@dataclass
class PermissionContext:
    user_id: str = ""
    chat_id: str = ""
    bot_id: str = ""
    level: PermissionLevel = PermissionLevel.USER
    is_private: bool = False
    group_id: str = ""
    role: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.level, PermissionLevel):
            try:
                self.level = PermissionLevel(str(self.level))
            except ValueError:
                self.level = PermissionLevel.USER
        self.user_id = str(self.user_id or "")
        self.chat_id = str(self.chat_id or "")
        self.bot_id = str(self.bot_id or "")
        self.group_id = str(self.group_id or "")
        self.role = str(self.role or "")

    @classmethod
    def system(cls, chat_id: str = "", bot_id: str = "") -> PermissionContext:
        """没有真实对话人的内部链路。"""
        return cls(
            user_id="",
            chat_id=chat_id,
            bot_id=bot_id,
            level=PermissionLevel.SYSTEM,
        )

    @classmethod
    def from_chat_args(cls, args) -> PermissionContext:
        """从 ``ChatArgs`` 构造上下文。

        普通聊天不信任 ``args.level``，一律按平台身份重新解析；只有内部链路
        （主动开口的 ``system``、定时任务保存的委托人等级）才接受显式等级。
        即使接受显式等级，主人身份仍会重新确认：``master`` 不能靠传字符串
        获得。
        """
        user_id = str(getattr(args, "user_id", "") or "")
        chat_id = str(getattr(args, "chat_id", "") or "")
        bot = getattr(args, "bot", None)
        bot_id = str(getattr(bot, "self_id", "") or "") if bot is not None else ""
        group_id = str(getattr(args, "group_id", "") or "")
        is_private = bool(getattr(args, "is_private", False))
        role = str(getattr(args, "role", "") or "")
        raw_level = str(getattr(args, "level", "") or "").strip().lower()
        if raw_level == PermissionLevel.SYSTEM.value:
            level = PermissionLevel.SYSTEM
        elif (
            raw_level in {item.value for item in PermissionLevel}
            and group_id
            and not is_private
        ):
            # 定时任务：委托人等级在创建时已确定，且带群上下文。
            level = PermissionLevel(raw_level)
            try:
                if is_master_user(user_id):
                    level = PermissionLevel.MASTER
                elif level is PermissionLevel.MASTER:
                    level = PermissionLevel.USER
            except Exception:
                level = PermissionLevel.USER
        else:
            level = PermissionResolver.resolve_level(
                user_id,
                group_id=group_id,
                is_private=is_private,
                role=role,
            )
        return cls(
            user_id=user_id,
            chat_id=chat_id,
            bot_id=bot_id,
            level=level,
            is_private=is_private,
            group_id=group_id,
            role=role,
        )


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    reason: str
    level: PermissionLevel
    scope: FunctionScope
    function: str
    action: str = ""
    chain: tuple[str, ...] = field(default_factory=tuple)
    pattern: str = ""

    def explain(self) -> str:
        # ``chain`` 的最后一环就是 ``reason``（由 ``_allow`` / ``_deny`` 追加），
        # 这里再拼一次会出现「… → 核心功能，始终可用 → 核心功能，始终可用」。
        if self.chain:
            return " → ".join(self.chain)
        return self.reason


class PermissionResolver:
    """按固定顺序求值，并给出可解释的原因。"""

    @classmethod
    def resolve_level(
        cls,
        user_id: str,
        group_id: str = "",
        is_private: bool = False,
        role: str = "",
    ) -> PermissionLevel:
        """把平台身份解析成三级之一。

        角色映射固定：``master`` → master，``owner`` / ``admin`` → admin，
        其余（``member`` / 未知 / 空）→ user。没有 guest 组。
        """
        uid = str(user_id or "").strip()
        if uid:
            try:
                if is_master_user(uid):
                    return PermissionLevel.MASTER
            except Exception as exc:
                log.debug(f"解析主人身份失败，按普通用户处理：{exc}")
        # 私聊里没有群上下文；群管理私聊也按 user，不能通过私聊管理群。
        if is_private:
            return PermissionLevel.USER
        if str(group_id or "").strip() and str(role or "").strip().lower() in {
            "owner",
            "admin",
        }:
            return PermissionLevel.ADMIN
        return PermissionLevel.USER

    @classmethod
    def resolve_scope(cls, func: Any, action: str | None = None) -> FunctionScope:
        """``scope_by_action[action]`` 优先，否则用功能默认作用域。"""
        action_key = str(action or "").strip().lower()
        mapping = getattr(func, "scope_by_action", None) or {}
        raw = mapping.get(action_key) if action_key else None
        if raw is None:
            raw = getattr(func, "scope", FunctionScope.BOT)
        try:
            return raw if isinstance(raw, FunctionScope) else FunctionScope(str(raw))
        except ValueError:
            return FunctionScope.BOT

    @classmethod
    def effective_level(cls, ctx: PermissionContext) -> PermissionLevel:
        """应用个人 ``downgrade_to`` 后的**有效**等级。

        功能内部需要按等级做判断或把等级写进持久化数据时（例如 ``remind``
        把委托人等级存进定时任务、``plan`` 判断能否覆盖固定作息），必须用
        这个结果，而不是 ``ctx.level``——否则个人降级只在权限求值里生效，
        这些地方仍按原始等级放行，降级形同虚设。
        """
        level = ctx.level
        personal = cls._get_user_override(ctx)
        if personal is None or personal.downgrade_to is None:
            return level
        target = personal.downgrade_to
        if (
            CAPABILITY_RANK[LEVEL_CAPABILITY[target]]
            < CAPABILITY_RANK[LEVEL_CAPABILITY[level]]
        ):
            return target
        return level

    @classmethod
    def check(
        cls,
        function_name: str,
        ctx: PermissionContext,
        action: str | None = None,
        *,
        record: bool = True,
    ) -> PermissionDecision:
        """求值入口。``record=False`` 用于 explain / 预览，不写拒绝埋点。"""
        token = _RECORD_DENIALS.set(bool(record))
        try:
            return cls._check(function_name, ctx, action)
        finally:
            _RECORD_DENIALS.reset(token)

    @classmethod
    def _check(
        cls,
        function_name: str,
        ctx: PermissionContext,
        action: str | None = None,
    ) -> PermissionDecision:
        from .function_calling import FunctionCallingManager

        chain: list[str] = []
        action_key = str(action or "").strip().lower()
        canonical = FunctionCallingManager.resolve_name(function_name)
        aliases = cls._aliases_of(canonical)
        func = FunctionCallingManager.function_calling.get(canonical)
        if func is None:
            return cls._deny(
                canonical,
                ctx,
                action_key,
                FunctionScope.BOT,
                "功能未注册",
                chain,
            )
        chain.append(f"功能 {canonical}")
        if bool(getattr(func, "essential", False)):
            return cls._allow(
                canonical,
                ctx,
                action_key,
                cls.resolve_scope(func, action_key),
                "核心功能，始终可用",
                chain,
            )
        if not FunctionCallingManager.is_manually_enabled(canonical):
            return cls._deny(
                canonical,
                ctx,
                action_key,
                cls.resolve_scope(func, action_key),
                "全局禁用",
                chain,
            )
        if not FunctionCallingManager.is_plugin_enabled(canonical):
            return cls._deny(
                canonical,
                ctx,
                action_key,
                cls.resolve_scope(func, action_key),
                "功能插件已关闭",
                chain,
            )

        level = ctx.level
        chain.append(f"等级 {level.value}")
        personal = cls._get_user_override(ctx)
        effective = cls.effective_level(ctx)
        if effective is not level:
            chain.append(f"个人降级 {level.value}→{effective.value}")
            level = effective
        elif personal and personal.downgrade_to is not None:
            chain.append("个人降级被忽略（只能向下）")

        scope = cls.resolve_scope(func, action_key)
        chain.append(f"作用域 {scope.value}")
        cap = LEVEL_CAPABILITY[level]
        if CAPABILITY_RANK[cap] < CAPABILITY_RANK[scope]:
            if level is PermissionLevel.ADMIN:
                reason = "管理不能使用 bot 全局功能"
            elif level is PermissionLevel.USER:
                reason = "用户只能正常聊天"
            else:
                reason = f"当前等级不能使用 {scope.value} 作用域的功能"
            return cls._deny(
                canonical,
                ctx,
                action_key,
                scope,
                reason,
                chain,
                level=level,
            )

        # 能力上限已满足；以下只是对「功能默认作用域」的覆盖。
        if personal:
            matched = cls._match_any(personal.deny, canonical, aliases)
            if matched:
                return cls._deny(
                    canonical,
                    ctx,
                    action_key,
                    scope,
                    f"个人禁止：{matched}",
                    chain,
                    level=level,
                    pattern=matched,
                )
            matched = cls._match_any(personal.allow, canonical, aliases)
            if matched:
                return cls._allow(
                    canonical,
                    ctx,
                    action_key,
                    scope,
                    f"个人允许：{matched}",
                    chain,
                    level=level,
                    pattern=matched,
                )

        level_override = cls._get_level_overrides().get(level)
        if level_override is not None:
            matched = cls._match_any(level_override.deny, canonical, aliases)
            if matched:
                return cls._deny(
                    canonical,
                    ctx,
                    action_key,
                    scope,
                    f"等级禁止：{matched}",
                    chain,
                    level=level,
                    pattern=matched,
                )
            matched = cls._match_any(level_override.allow, canonical, aliases)
            if matched:
                return cls._allow(
                    canonical,
                    ctx,
                    action_key,
                    scope,
                    f"等级允许：{matched}",
                    chain,
                    level=level,
                    pattern=matched,
                )

        return cls._allow(
            canonical,
            ctx,
            action_key,
            scope,
            "功能默认作用域允许",
            chain,
            level=level,
        )

    @classmethod
    def explain(
        cls,
        function_name: str,
        ctx: PermissionContext,
        action: str | None = None,
    ) -> str:
        return cls.check(function_name, ctx, action, record=False).explain()

    # ------------------------------------------------------------------
    # 配置读取
    # ------------------------------------------------------------------

    @classmethod
    def _get_level_overrides(cls) -> dict[PermissionLevel, LevelOverride]:
        """读取等级覆盖表；缺省就是「不覆盖」。"""
        raw: dict[str, Any] = {}
        try:
            from ..config import config

            value = config.get("permission")
            if isinstance(value, dict):
                raw = value
        except Exception as exc:
            log.debug(f"读取等级权限覆盖失败，按功能默认作用域处理：{exc}")

        overrides: dict[PermissionLevel, LevelOverride] = {}
        for level in (
            PermissionLevel.MASTER,
            PermissionLevel.ADMIN,
            PermissionLevel.USER,
            PermissionLevel.SYSTEM,
        ):
            item = raw.get(level.value)
            item = item if isinstance(item, dict) else {}
            overrides[level] = LevelOverride(
                level=level,
                label=str(item.get("label") or LEVEL_LABELS[level.value]),
                allow=cls._as_patterns(item.get("allow")),
                deny=cls._as_patterns(item.get("deny")),
            )
        return overrides

    @classmethod
    def _get_user_override(cls, ctx: PermissionContext) -> UserOverride | None:
        try:
            from ..config import config

            raw = config.get("permission_users")
        except Exception as exc:
            log.debug(f"读取个人权限覆盖失败：{exc}")
            return None
        if not isinstance(raw, dict) or not raw:
            return None

        candidates: list[str] = []
        for value in (ctx.user_id, resolve_account_id(ctx.user_id)):
            value = str(value or "")
            if value and value not in candidates:
                candidates.append(value)
        for uid in candidates:
            item = raw.get(uid)
            if not isinstance(item, dict):
                continue
            downgrade = cls._parse_downgrade(item.get("downgrade_to"))
            return UserOverride(
                user_id=uid,
                allow=cls._as_patterns(item.get("allow")),
                deny=cls._as_patterns(item.get("deny")),
                downgrade_to=downgrade,
            )
        return None

    @classmethod
    def _parse_downgrade(cls, value: Any) -> PermissionLevel | None:
        text = str(value or "").strip().lower()
        if not text:
            return None
        if text in {PermissionLevel.ADMIN.value, PermissionLevel.USER.value}:
            return PermissionLevel(text)
        log.warning(
            f"个人权限 downgrade_to='{text}' 无效：只能降为 admin / user，已忽略"
        )
        return None

    @staticmethod
    def _as_patterns(value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            return (value,) if value.strip() else ()
        if isinstance(value, list | tuple):
            return tuple(str(item).strip() for item in value if str(item).strip())
        return ()

    @staticmethod
    def _aliases_of(function_name: str) -> tuple[str, ...]:
        """功能的旧名 / 别名；权限规则同时匹配规范名与别名。"""
        try:
            from .function_calling import FunctionCallingManager

            func = FunctionCallingManager.function_calling.get(function_name)
            return tuple(
                str(alias).strip()
                for alias in (getattr(func, "aliases", ()) or ())
                if str(alias).strip()
            )
        except Exception:
            return ()

    @classmethod
    def _match_any(
        cls,
        patterns: tuple[str, ...],
        function_name: str,
        aliases: tuple[str, ...] = (),
    ) -> str:
        for pattern in patterns:
            if cls._pattern_matches(pattern, function_name, aliases):
                return pattern
        return ""

    @staticmethod
    def _pattern_matches(
        pattern: str,
        function_name: str,
        aliases: tuple[str, ...] = (),
    ) -> bool:
        if not pattern:
            return False
        if pattern == "*":
            return True
        names = (function_name, *aliases)
        if pattern in names:
            return True
        owner = ""
        try:
            from .function_calling import FunctionCallingManager

            owner = FunctionCallingManager.owner_of(function_name)
        except Exception:
            owner = ""
        if owner and pattern == owner:
            return True
        if pattern.endswith(".*"):
            prefix = pattern[:-2]
            if not prefix:
                return True
            return (
                any(name.startswith(f"{prefix}.") for name in names) or owner == prefix
            )
        return any(fnmatchcase(name, pattern) for name in names)

    # ------------------------------------------------------------------
    # 结果构造
    # ------------------------------------------------------------------

    @classmethod
    def _allow(
        cls,
        function_name: str,
        ctx: PermissionContext,
        action: str,
        scope: FunctionScope,
        reason: str,
        chain: list[str],
        *,
        level: PermissionLevel | None = None,
        pattern: str = "",
    ) -> PermissionDecision:
        chain = [*chain, reason]
        return PermissionDecision(
            allowed=True,
            reason=reason,
            level=level or ctx.level,
            scope=scope,
            function=function_name,
            action=action,
            chain=tuple(chain),
            pattern=pattern,
        )

    @classmethod
    def _deny(
        cls,
        function_name: str,
        ctx: PermissionContext,
        action: str,
        scope: FunctionScope,
        reason: str,
        chain: list[str],
        *,
        level: PermissionLevel | None = None,
        pattern: str = "",
    ) -> PermissionDecision:
        chain = [*chain, reason]
        decision = PermissionDecision(
            allowed=False,
            reason=reason,
            level=level or ctx.level,
            scope=scope,
            function=function_name,
            action=action,
            chain=tuple(chain),
            pattern=pattern,
        )
        if _RECORD_DENIALS.get():
            try:
                from .telemetry import telemetry

                telemetry.record(
                    "permission_denied",
                    function=function_name,
                    action=action or None,
                    level=decision.level.value,
                    scope=scope.value,
                    reason=reason,
                    user_id=ctx.user_id or None,
                    chat_id=ctx.chat_id or None,
                    group_id=ctx.group_id or None,
                )
            except Exception:
                pass
        agent_log.debug(f"权限拒绝 {function_name}: {decision.explain()}")
        return decision


__all__ = [
    "CAPABILITY_RANK",
    "DEFAULT_USER_PERMISSION_ITEM",
    "LEVEL_CAPABILITY",
    "LEVEL_LABELS",
    "Capability",
    "FunctionScope",
    "LevelOverride",
    "PermissionContext",
    "PermissionDecision",
    "PermissionLevel",
    "PermissionResolver",
    "UserOverride",
]
