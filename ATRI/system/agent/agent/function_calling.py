from typing import ClassVar

import anyio
from pydantic import BaseModel

from ATRI.exceptions import str_traceback
from ATRI.utils.datetime import now

from ..config import config
from ..event import AgentEventBus
from ..llm import ModelType, llm_manager
from ..utils import log, request
from .function_permission import (
    FunctionScope,
    PermissionContext,
    PermissionLevel,
    PermissionResolver,
)
from .history import History
from .memes import FaceManager
from .memory.manage import memory_manager
from .memory.persona import tag_emotion
from .sender import ChatSender, OneBotChatSender
from .state import feel
from .telemetry import telemetry
from .user import add_self_memory, get_user_info, save_user_info
from .user_profile import UserProfile

#: 单个对话人「临时长期记忆」的条数上限（`remember` 写入时强制，先进先出）。
#: 这份列表每轮整段进提示词，所以上限是成本约束，不只是整洁问题。
USER_MEMORY_LIMIT = 11

#: 亚托莉自我记忆的条数上限。
SELF_MEMORY_LIMIT = 8

#: 单条回复的字数上限（兼容旧常量）。
#:
#: 实际运行时以 `AgentConfig.reply_max_chars` 为准；`reply_pipeline.split`
#: 会读取配置。这里保留常量供旧调用与测试引用。
REPLY_MAX_CHARS = 300


def _trim_reply(text: str) -> tuple[str, str]:
    """兼容旧代码：把过长文本截短，返回 (新文本, 提示语)。

    P5 之后实际清洗在 `reply_pipeline`；这里保留给旧调用方，
    行为与 `split` 的句子边界截断一致。
    """
    from .reply_pipeline import split

    body = (text or "").strip()
    if len(body) <= REPLY_MAX_CHARS:
        return body, ""
    parts = split(body, REPLY_MAX_CHARS)
    trimmed = parts[0] if parts else ""
    note = (
        f"已发送，但内容过长被截断（原 {len(body)} 字，只发了 {len(trimmed)} 字）。"
        "下次想说的多，就分几次调用 say，每条一句。"
    )
    return trimmed, note


class FunctionCallingArg:
    def __init__(self, name: str, _type: str, description: str):
        self.name = name
        self.type = _type
        self.description = description


def _context_for(user_id: str = "", chat_id: str = "", bot=None) -> PermissionContext:
    """没有显式权限上下文时的兜底。

    有真实用户 ID 就按该用户求值（旧调用只传 sender / user_id）；
    完全没有对话人（后台、测试、主动开口）时按 ``system`` 处理——
    保持旧行为，而不是把所有无参调用都判成 ``user`` 后大面积不可用。
    """
    uid = str(user_id or "").strip()
    if not uid:
        return PermissionContext.system(
            chat_id=str(chat_id or ""),
            bot_id=str(getattr(bot, "self_id", "") or "") if bot is not None else "",
        )
    return PermissionContext(
        user_id=uid,
        chat_id=str(chat_id or ""),
        bot_id=str(getattr(bot, "self_id", "") or "") if bot is not None else "",
        level=PermissionResolver.resolve_level(uid),
    )


class FunctionCallingData:
    def __init__(
        self,
        sender_id: str,
        chat_id: str,
        sender: ChatSender,
        data: dict,
        current_history: History | None = None,
        bot=None,
        permission: PermissionContext | None = None,
    ):
        self.sender_id = sender_id
        self.chat_id = chat_id
        self.sender = sender
        self.data = data
        self.current_history = current_history
        self._bot = bot
        self.permission = permission or _context_for(sender_id, chat_id, bot)

    @property
    def bot(self):
        """当前这次对话用的 bot。

        **必须显式传进来**：`data` 只是本次调用的参数 (`dict`)，里面没有
        bot。之前误以为它是 `ChatArgs`、用 `getattr(self.data, "bot")` 去取，
        结果永远是 `None`，定时任务存下来的 `bot_id` 一直是空串——到点只能
        随便挑一个 bot 发，发到不相关的群上就是「群不存在」。

        定时任务必须记住**创建它的那个 bot**：多实例部署下，
        到点换另一个身份发言是错的。
        """
        return self._bot if self._bot is not None else getattr(self.data, "bot", None)

    @property
    def bot_id(self) -> str:
        """bot 的标识，用于存进定时任务并精确匹配。"""
        bot = self.bot
        return str(getattr(bot, "self_id", "") or "") if bot is not None else ""

    @property
    def mtype(self) -> int:
        """对话通道的协议类型：0 = OneBot，1 = QQ 官方。"""
        return int(getattr(self.sender, "mtype", 0) or 0)

    @property
    def mention_target(self) -> str:
        """要 @ 的人。"""
        return str(self.sender_id or "").strip()


class EnableCheck(BaseModel):
    """技能/功能的环境可用性判定结果"""

    available: bool = True
    reason: str = ""
    """不可用原因，供 UI 灰显 tooltip 与排障使用"""


class FunctionCalling:
    function_name = ""
    aliases: ClassVar[tuple[str, ...]] = ()
    """旧名 / 别名。解析时归一到 ``function_name``，提示词与回填只用新名。"""
    description = ""
    args: ClassVar[list[FunctionCallingArg]] = []
    essential: ClassVar[bool] = False
    """核心功能：始终启用，不受手动开关影响（见 FunctionCallingManager）"""
    prompt_hidden: ClassVar[bool] = False
    """不进入常规功能提示词，但仍可被模型直接调用。

    与插件 `rare_functions` 的区别：rare 只能经 `call` 调用（受 bot 权限约束），
    `prompt_hidden` 只是不给提示词占位，例如仅在技能索引被截断时才提示的
    `skills()`。
    """
    scope: ClassVar[FunctionScope] = FunctionScope.BOT
    """默认作用域。忘记声明时按 ``bot`` 处理——最安全，普通用户不会误用。"""
    scope_by_action: ClassVar[dict[str, FunctionScope]] = {}
    """按动作细分作用域，例如读本群 ``chat``、写本群 ``group``。"""
    returns_data: ClassVar[bool] = True
    """调用结果是否需要回填给思考。

    ``False`` 表示副作用调用（发消息 / 发表情 / 改状态），派发后立即返回，
    结果不进入下一轮上下文。默认 ``True``：忘记声明时行为退化为「安全但慢」，
    而不是把真实结果悄悄丢掉。
    """
    parallel_safe: ClassVar[bool] = True
    """同一轮里能否与其他数据调用并发执行。

    发送类必须是 ``False``（保持模型输出顺序）；会改共享状态或调用模型的
    生活系统写操作也应为 ``False``，由执行器放进串行 lane。
    """

    @classmethod
    def can_enable(cls) -> EnableCheck:
        """① 当前环境能否开启该功能（与对话人无关）。"""
        return EnableCheck()

    @classmethod
    def is_available_for(cls, sender: ChatSender | None, user_id: str) -> bool:
        """② 对当前对话人是否可用。sender 可能为 None（后台/测试调用），必须容忍。"""
        return True

    @classmethod
    def action_for(cls, data: FunctionCallingData) -> str:
        """③ 本次调用的动作名，用于 ``scope_by_action``。默认无动作。"""
        return ""

    @staticmethod
    async def call(data: FunctionCallingData):
        pass


class FunctionCallingUnavailableError(Exception):
    """FC 存在但当前不可用（手动关闭 / 环境不支持 / 当前对话人不可用）"""


class FunctionCallingEssentialError(Exception):
    """核心功能不可关闭（say 等）"""


def _plugin_enabled(function_name: str) -> bool:
    """功能所属插件的整体开关；不属于任何插件时恒为 True。"""
    from .function_plugin.manage import function_plugin_manager

    return function_plugin_manager.is_enabled(
        function_plugin_manager.owner_of_function(function_name)
    )


def _as_bool(value) -> bool:
    """参数可能是 bool，也可能是 "true"/"1"/"是"（模型输出是文本）。"""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("true", "1", "yes", "是")


def _with_mention(text: str, target: str, mtype: int):
    """把「@某人」拼到正文前面，返回该适配器的 ``Message``。"""
    who = str(target or "").strip()
    if not who:
        return text
    try:
        if mtype == 1:
            from nonebot.adapters.qq import Message as QQMsg
            from nonebot.adapters.qq import MessageSegment as QQSegment

            return QQMsg(QQSegment.mention_user(who)) + QQMsg(QQSegment.text(text))
        from nonebot.adapters.onebot.v11 import Message as OMsg
        from nonebot.adapters.onebot.v11 import MessageSegment as OSegment

        return OMsg(OSegment.at(who)) + OMsg(OSegment.text(text))
    except Exception as exc:
        log.debug(f"构造 @{who} 失败，本条不加 @：{exc}")
        return text


def _plugin_rare(function_name: str) -> bool:
    """该功能是否被功能插件声明为不常用（只能经 call 调用）。"""
    from .function_plugin.manage import function_plugin_manager

    return function_plugin_manager.is_rare_function(function_name)


class FunctionCallingManager:
    function_calling: ClassVar[dict[str, type[FunctionCalling]]] = {}
    alias_index: ClassVar[dict[str, str]] = {}
    """旧名 / 别名 → 规范名。注册时建立，解析后统一用规范名。"""
    _alias_warned: ClassVar[set[str]] = set()

    @classmethod
    def register(cls, func: type[FunctionCalling]):
        cls.function_calling[func.function_name] = func
        aliases = {
            str(alias).strip()
            for alias in (getattr(func, "aliases", ()) or ())
            if str(alias).strip()
        }
        # 同名类重新注册时，清掉已经不再声明的旧别名，避免指向过期实现。
        for alias, canonical in list(cls.alias_index.items()):
            if canonical == func.function_name and alias not in aliases:
                cls.alias_index.pop(alias, None)
        for alias in aliases:
            if alias != func.function_name:
                cls.alias_index[alias] = func.function_name
        if getattr(func, "essential", False):
            cls.purge_essential_disables()

    @classmethod
    def clear_all(cls):
        cls.function_calling.clear()
        cls.alias_index.clear()
        cls._alias_warned.clear()

    @classmethod
    def unregister(cls, name: str) -> type[FunctionCalling] | None:
        """摘除一个功能及其全部别名；插件回滚 / 卸载时使用。"""
        canonical = cls.resolve_name(name)
        func = cls.function_calling.pop(canonical, None)
        for alias, target in list(cls.alias_index.items()):
            if target == canonical:
                cls.alias_index.pop(alias, None)
        return func

    @classmethod
    def resolve_name(cls, name: str) -> str:
        """把旧名 / 别名解析成规范名；未注册的名字原样返回。"""
        key = str(name or "").strip()
        if key in cls.function_calling:
            return key
        canonical = cls.alias_index.get(key)
        if canonical and canonical in cls.function_calling:
            if key not in cls._alias_warned:
                cls._alias_warned.add(key)
                log.debug(f"功能调用名 {key} 是旧名，已归一为 {canonical}")
            return canonical
        return key

    @classmethod
    def _disabled_set(cls) -> set[str]:
        return {
            cls.resolve_name(name)
            for name in (config.get("function_calling_disable") or [])
        }

    # ---- 手动开关（存 AgentConfig 的禁用列表：默认启用，列表只记例外）----

    @classmethod
    def is_essential(cls, name: str) -> bool:
        """核心功能：维持对话闭环所必需，永远启用，不能被手动关闭。"""
        func = cls.function_calling.get(cls.resolve_name(name))
        return bool(getattr(func, "essential", False))

    @classmethod
    def is_rare(cls, name: str) -> bool:
        """不常用功能：不进入常规功能提示词，只能由 call 调用。"""
        return _plugin_rare(cls.resolve_name(name))

    @classmethod
    def is_manually_enabled(cls, name: str) -> bool:
        canonical = cls.resolve_name(name)
        if cls.is_essential(canonical):
            return True
        return canonical not in cls._disabled_set()

    @classmethod
    def is_plugin_enabled(cls, name: str) -> bool:
        return _plugin_enabled(cls.resolve_name(name))

    @classmethod
    def owner_of(cls, name: str) -> str:
        """功能所属插件名；不属于任何插件时为空串。"""
        from .function_plugin.manage import function_plugin_manager

        return function_plugin_manager.owner_of_function(cls.resolve_name(name)) or ""

    @classmethod
    def set_manually_enabled(cls, name: str, enabled: bool):
        canonical = cls.resolve_name(name)
        if not enabled and cls.is_essential(canonical):
            raise FunctionCallingEssentialError(
                f"功能 {canonical} 是核心功能，不能关闭"
            )
        # config.get() 返回内部引用，必须构造新列表后 config.set 才会落盘
        names = list(config.get("function_calling_disable") or [])
        # 旧名与规范名视为同一个开关，避免迁移期出现「关了一个另一个还开着」。
        names = [item for item in names if cls.resolve_name(item) != canonical]
        if not enabled:
            names.append(canonical)
        config.set("function_calling_disable", names)

    @classmethod
    def purge_essential_disables(cls) -> list[str]:
        """把核心功能名从禁用列表中剔除（手动改配置 / 旧版本残留都会在这里被纠正）。

        返回被修复后的列表；没有变化时不写盘。
        """
        names = list(config.get("function_calling_disable") or [])
        kept: list[str] = []
        for name in names:
            canonical = cls.resolve_name(name)
            if cls.is_essential(canonical) or canonical in kept:
                continue
            kept.append(canonical)
        if kept != names:
            config.set("function_calling_disable", kept)
        return kept

    # ---- 可用性（手动开关 ∧ 环境 ∧ 对话人）----

    @classmethod
    def get_can_enable(cls, name: str) -> EnableCheck:
        canonical = cls.resolve_name(name)
        func = cls.function_calling.get(canonical)
        if func is None:
            return EnableCheck(available=False, reason="功能未注册")
        try:
            return func.can_enable()
        except Exception as e:
            log.warning(f"功能 {canonical} 环境可用性检查失败:{str_traceback(e)}")
            return EnableCheck(available=False, reason="环境可用性检查失败")

    @classmethod
    def _context(
        cls,
        sender: ChatSender | None = None,
        user_id: str = "",
        permission: PermissionContext | None = None,
    ) -> PermissionContext:
        if permission is not None:
            return permission
        return _context_for(user_id)

    @classmethod
    def availability(
        cls,
        name: str,
        sender: ChatSender | None = None,
        user_id: str = "",
        permission: PermissionContext | None = None,
        action: str | None = None,
        *,
        record: bool = True,
    ) -> EnableCheck:
        """功能是否可用，并给出**具体原因**。

        判定顺序：注册 → 权限（含全局/插件开关、能力上限、个人与等级规则）
        → 环境 → 对话人。

        :param record: 是否写拒绝埋点。列清单（``get_available``）只是求值，
            不是「模型试图越权」，必须传 ``False``——否则每处理一条消息都会
            为每个 bot 作用域功能各写一条 ``permission_denied``，把真实拒绝淹掉。
        """
        canonical = cls.resolve_name(name)
        func = cls.function_calling.get(canonical)
        if func is None:
            return EnableCheck(available=False, reason="功能未注册")

        ctx = cls._context(sender, user_id, permission)
        decision = PermissionResolver.check(canonical, ctx, action, record=record)
        if not decision.allowed:
            return EnableCheck(available=False, reason=decision.reason)

        check = cls.get_can_enable(canonical)
        if not check.available:
            return check
        try:
            if not bool(func.is_available_for(sender, user_id)):
                return EnableCheck(available=False, reason="当前对话人不可用")
        except Exception as e:
            log.warning(f"功能 {canonical} 对话人可用性检查失败:{str_traceback(e)}")
            return EnableCheck(available=False, reason="对话人可用性检查失败")
        return EnableCheck()

    @classmethod
    def is_available(
        cls,
        name: str,
        sender: ChatSender | None = None,
        user_id: str = "",
        permission: PermissionContext | None = None,
        action: str | None = None,
        *,
        record: bool = True,
    ) -> bool:
        return cls.availability(
            name, sender, user_id, permission, action, record=record
        ).available

    @classmethod
    def get_available(
        cls,
        sender: ChatSender | None = None,
        user_id: str = "",
        permission: PermissionContext | None = None,
    ) -> dict[str, type[FunctionCalling]]:
        """保持注册顺序（= 提示词顺序）的可用功能子集。

        **求值不写拒绝埋点**：这里只是在拼提示词，模型并没有试图调用这些
        功能。写埋点会让每条消息都产出 4~6 条 ``permission_denied``，
        真正的越权尝试反而查不出来。
        """
        return {
            name: func
            for name, func in cls.function_calling.items()
            if not _plugin_rare(name)
            and not bool(getattr(func, "prompt_hidden", False))
            and cls.availability(
                name, sender, user_id, permission, record=False
            ).available
        }

    @classmethod
    def get_rare_available(
        cls,
        sender: ChatSender | None = None,
        user_id: str = "",
        permission: PermissionContext | None = None,
    ) -> dict[str, type[FunctionCalling]]:
        """可由 call 调用的不常用功能子集（不进常规提示词）。求值同样不写埋点。"""
        return {
            name: func
            for name, func in cls.function_calling.items()
            if name != "call"
            and _plugin_rare(name)
            and cls.availability(
                name, sender, user_id, permission, record=False
            ).available
        }

    @classmethod
    def hidden_available(
        cls,
        sender: ChatSender | None = None,
        user_id: str = "",
        permission: PermissionContext | None = None,
    ) -> dict[str, type[FunctionCalling]]:
        """``prompt_hidden`` 但仍可直接调用、且当前可用的功能。

        它们不进常规清单，但提示词里点名要求使用（例如 ``say_exact``），
        需要单独列出来，否则会和「禁止调用未列出的功能」自相矛盾。
        """
        return {
            name: func
            for name, func in cls.function_calling.items()
            if bool(getattr(func, "prompt_hidden", False))
            and cls.availability(
                name, sender, user_id, permission, record=False
            ).available
        }

    @classmethod
    def action_for(cls, name: str, data: FunctionCallingData) -> str:
        canonical = cls.resolve_name(name)
        func = cls.function_calling.get(canonical)
        if func is None:
            return ""
        try:
            return str(func.action_for(data) or "")
        except Exception as exc:
            log.debug(f"功能 {canonical} 动作解析失败，按默认作用域处理：{exc}")
            return ""

    @classmethod
    async def call(cls, func_name: str, data: FunctionCallingData):
        canonical = cls.resolve_name(func_name)
        if canonical not in cls.function_calling:
            raise KeyError(func_name)
        action = cls.action_for(canonical, data)
        decision = PermissionResolver.check(canonical, data.permission, action)
        if not decision.allowed:
            raise FunctionCallingUnavailableError(
                f"功能 {canonical} 当前不可用：{decision.reason}"
            )
        if not cls.is_available(
            canonical,
            data.sender,
            data.sender_id,
            data.permission,
            action,
            record=False,
        ):
            check = cls.availability(
                canonical,
                data.sender,
                data.sender_id,
                data.permission,
                action,
                record=False,
            )
            raise FunctionCallingUnavailableError(
                f"功能 {canonical} 当前不可用：{check.reason or '未知原因'}"
            )
        return await cls.function_calling[canonical].call(data)


#: 表情候选清单；实际由 `action_executor._available_faces()` 在投递时读取，
#: 这里保留旧变量供插件/测试引用。
meme_examples = []
for meme in FaceManager.get_all_memes():
    desc = FaceManager.get_meme_description(meme)
    if desc:
        # 只取描述的前一部分，避免过长
        short_desc = desc.split("（")[0] if "（" in desc else desc[:20]
        meme_examples.append(f"{meme}:{short_desc}")
meme_example_str = ";".join(meme_examples)


def register_function_calling():
    FunctionCallingManager.clear_all()

    class SendMessageFunctionCalling(FunctionCalling):
        function_name = "say"
        aliases = ("send_message",)
        scope = FunctionScope.CHAT
        returns_data = False
        parallel_safe = False
        description = (
            "在聊天里说话。\n"
            "说出你的意图，不用雕琢措辞：intent 写「想表达什么」，"
            "例如「解释钓鱼要先抛钩再收线」，"
            "不要写「你好呀，钓鱼要先抛钩哦」这种最终台词。\n"
            "最终怎么说由聊天模型决定；想分几条说就用 count，不要重复调用。\n"
            "如果此刻不想说话，就不要再调用任何功能，也不要用正文代替。"
        )
        essential = True
        args: ClassVar = [
            FunctionCallingArg(
                name="intent",
                _type="str",
                description=(
                    "你要表达的意思。像在群里想说什么，不是写文章：\n"
                    "  1. 短。一句通常 5-20 字；想说的多就 count=2/3，别一口气写一大段\n"
                    "  2. 口语。想好语气词与书面语的区别，但不必自己写最终台词\n"
                    "  3. 有反应先于有内容。别人说「今天好累」，表达「关心一下」"
                    "比「建议注意休息」像人\n"
                    "  4. 不写动作描写、不写括号里的心理活动、不列 1234\n"
                    "  5. 不要复述对方刚说的话"
                ),
            ),
            FunctionCallingArg(
                name="tone",
                _type="str",
                description="(可选)语气，如「轻松」「敷衍」「认真」；留空按自然语气",
            ),
            FunctionCallingArg(
                name="count",
                _type="int",
                description="(可选)希望分几条说，1-3，默认 1",
            ),
            FunctionCallingArg(
                name="at",
                _type="bool",
                description="(可选)置为 true 时 @ 正在跟你说话的人；"
                "私聊、或本来就是在直接回答他时不必加。",
            ),
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            """降级直发路径。

            正常链路由 `ChatOutbox` 渲染后发送；只有绕过 Outbox 的旧调用
            （测试 / 插件直接调用）才走这里：把 intent 清洗后当最终文本发。
            """
            from .reply_pipeline import sanitize, split

            intent = str(data.data.get("intent") or "").strip()
            current_history = data.current_history
            if current_history is None:
                raise RuntimeError("发送消息时缺少关联的聊天历史")
            if not intent:
                return None
            count = data.data.get("count") or 1
            try:
                count = max(1, min(3, int(count)))
            except (TypeError, ValueError):
                count = 1
            messages = split(sanitize(intent), REPLY_MAX_CHARS)[:count]
            if not messages:
                return None
            if _as_bool(data.data.get("at")):
                payload = _with_mention(messages[0], data.mention_target, data.mtype)
            else:
                payload = messages[0]
            try:
                await data.sender.send(payload)
            except Exception:
                return "发送失败,请勿再次尝试"
            current_history.add_response(messages[0])
            return None

    FunctionCallingManager.register(SendMessageFunctionCalling)

    class SayExactFunctionCalling(FunctionCalling):
        function_name = "say_exact"
        scope = FunctionScope.CHAT
        returns_data = False
        parallel_safe = False
        prompt_hidden = True
        description = (
            "逐字发送内容，唯一跳过聊天模型的发送入口。"
            "只用于代码、命令、配置片段、引用原文或用户明确要求「原样发」。"
            "普通聊天不要用它；想说什么用 say(intent=...)。"
        )
        args: ClassVar[list[FunctionCallingArg]] = [
            FunctionCallingArg(
                name="text", _type="str", description="要逐字发送的内容"
            ),
            FunctionCallingArg(
                name="at",
                _type="bool",
                description="(可选)置为 true 时 @ 正在跟你说话的人",
            ),
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            """降级直发路径；正常链路由 Outbox 以 exact job 发送。"""
            from .reply_pipeline import sanitize

            text = str(data.data.get("text") or "").strip()
            current_history = data.current_history
            if current_history is None:
                raise RuntimeError("发送消息时缺少关联的聊天历史")
            if not text:
                return None
            # 逐字发送唯一允许的清洗只有去代码围栏，不做说话人/动作清洗
            try:
                await data.sender.send(text)
            except Exception:
                return "发送失败,请勿再次尝试"
            current_history.add_response(sanitize(text) or text)
            return None

    FunctionCallingManager.register(SayExactFunctionCalling)

    class CallFunctionCalling(FunctionCalling):
        function_name = "call"
        scope = FunctionScope.BOT
        description = (
            "调用不常用功能。不常用功能不会出现在上面的功能列表里，"
            "它们的具体用法通常写在技能正文中，请按技能给出的功能名与参数调用"
        )
        args: ClassVar[list[FunctionCallingArg]] = [
            FunctionCallingArg(
                name="function", _type="str", description="要调用的功能名"
            ),
            FunctionCallingArg(
                name="args",
                _type="dict",
                description='目标功能的参数，写成 {"参数名": 值} 的字典，无参数时省略',
            ),
        ]

        @classmethod
        def can_enable(cls) -> EnableCheck:
            if not FunctionCallingManager.get_rare_available():
                return EnableCheck(available=False, reason="没有可用的不常用功能")
            return EnableCheck()

        @classmethod
        def describe_rare(cls) -> str:
            """当前会话可调用的不常用功能清单。"""
            rare = FunctionCallingManager.get_rare_available()
            if not rare:
                return ""
            from .function_plugin.manage import function_plugin_manager

            lines: list[str] = []
            for name, func in rare.items():
                owner = function_plugin_manager.owner_of_function(name)
                label = f"（属于功能插件 {owner}）" if owner else ""
                lines.append(f"- {name}{label}: {func.description}")
            return "\n可调用的不常用功能:\n" + "\n".join(lines)

        @staticmethod
        async def call(data: FunctionCallingData):
            from ..brain.action_parser import normalize_args

            requested = str(data.data.get("function") or "").strip()
            if not requested:
                return "请提供要调用的功能名"
            func_name = FunctionCallingManager.resolve_name(requested)
            if func_name == "call":
                return "call 不能调用自己"
            required = data.data.get("args")
            if required is None:
                required = {}
            if not isinstance(required, dict):
                return 'args 必须是 {"参数名": 值} 形式的字典'
            func_cls = FunctionCallingManager.function_calling.get(func_name)
            if func_cls is None:
                known = "、".join(FunctionCallingManager.get_rare_available()) or "无"
                return f"未知的功能:{func_name}，当前可调用的不常用功能:{known}"
            if not FunctionCallingManager.is_rare(func_name):
                return f"功能 {func_name} 不是不常用功能，请直接调用它"
            if not FunctionCallingManager.is_available(
                func_name, data.sender, data.sender_id, data.permission
            ):
                # 第一次求值已经写过拒绝埋点，这里只取原因，不要重复记。
                check = FunctionCallingManager.availability(
                    func_name,
                    data.sender,
                    data.sender_id,
                    data.permission,
                    record=False,
                )
                return (
                    f"功能 {func_name} 当前不可用：{check.reason or '该功能已被关闭'}"
                )
            args, issues = normalize_args(func_cls, required)
            if issues:
                return f"功能 {func_name} 的参数不合规：{'；'.join(issues)}"
            return await FunctionCallingManager.call(
                func_name,
                FunctionCallingData(
                    data.sender_id,
                    data.chat_id,
                    data.sender,
                    args,
                    data.current_history,
                    bot=data.bot,
                    permission=data.permission,
                ),
            )

    FunctionCallingManager.register(CallFunctionCalling)

    class SendMdMessageFunctionCalling(FunctionCalling):
        function_name = "md"
        aliases = ("send_md_message",)
        scope = FunctionScope.CHAT
        returns_data = False
        parallel_safe = False
        description = (
            "发一张 Markdown 卡片，用于专业性回答或需要排版的说明。\n"
            "intent 写「这张卡片要表达什么」，不要自己写最终排版；"
            "标题与正文由聊天模型决定。"
        )
        args: ClassVar = [
            FunctionCallingArg(
                name="intent", _type="str", description="这张卡片要表达什么"
            ),
            FunctionCallingArg(
                name="image",
                _type="str",
                description="(可选)已经确定好的图片 URL/路径；没有就不要填，禁止编造",
            ),
            FunctionCallingArg(
                name="title",
                _type="str",
                description="(可选)希望使用的标题；留空由聊天模型决定",
            ),
            FunctionCallingArg(
                name="tone", _type="str", description="(可选)语气，如「认真」「轻松」"
            ),
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            """降级直发路径：不做 Chat 渲染，直接把 intent 当正文发卡片。"""
            from .function_calling_utils import send_markdown_document
            from .reply_pipeline import parse_markdown, sanitize

            intent = str(data.data.get("intent") or "").strip()
            current_history = data.current_history
            if current_history is None:
                raise RuntimeError("发送消息时缺少关联的聊天历史")
            if not intent:
                return None
            fallback = sanitize(intent)
            image = str(data.data.get("image") or "").strip()
            title = str(data.data.get("title") or "").strip()
            parsed = parse_markdown("", fallback, image)
            if title and not parsed["title"]:
                parsed["title"] = title[:40]
            if not parsed["body"]:
                return None
            try:
                md_file = await send_markdown_document(data, parsed)
            except Exception as e:
                log.warning(f"MD发送发生错误:{str_traceback(e)}")
                return "发送失败,请勿再次尝试"
            current_history.add_response(f"[md:{md_file}]")
            return None

    FunctionCallingManager.register(SendMdMessageFunctionCalling)

    class ReadMdMessageFunctionCalling(FunctionCalling):
        function_name = "md_read"
        aliases = ("read_md_message",)
        scope = FunctionScope.CHAT
        description = "获取你发送的md格式消息"
        args: ClassVar = [
            FunctionCallingArg(name="file", _type="str", description="文件名")
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            md_file = data.data.get("file", "")
            try:
                from ..service import plugin

                md_path = (
                    plugin.get_path() / "chat" / str(data.chat_id) / "md" / md_file
                )
                async with await anyio.open_file(md_path, "r", encoding="utf-8") as f:
                    content = await f.read()
                return content
            except Exception:
                return "获取失败"

    FunctionCallingManager.register(ReadMdMessageFunctionCalling)

    class LoveChangeFunctionCalling(FunctionCalling):
        function_name = "love"
        aliases = ("love_change",)
        scope = FunctionScope.CHAT
        returns_data = False
        parallel_safe = True
        description = (
            "更改你对用户的好感度（长期关系评价）。"
            "注意：好感度不会改变你当下的心情——心情用 feel 记录。"
        )
        args: ClassVar = [
            FunctionCallingArg(
                name="num", _type="int", description="与对话人好感度增减数值"
            )
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            # 处理好感度变化
            num = data.data.get("num", 0)
            if num:
                user_info = get_user_info(data.sender_id)
                user_info.love = user_info.love + num
                save_user_info(data.sender_id, user_info)
                return (
                    f"用户 {data.sender_id} 好感度变化：{num}, 当前值：{user_info.love}"
                )

    FunctionCallingManager.register(LoveChangeFunctionCalling)

    class RelationSetFunctionCalling(FunctionCalling):
        function_name = "rel_set"
        aliases = ("relation_set",)
        scope = FunctionScope.CHAT
        returns_data = False
        parallel_safe = True
        description = (
            "设置你对某个人的关系态度（不是心情，也不是好感度）。"
            "用于记录「刚吵完架」「在闹别扭」「最近很黏人」这类针对某个人的状态。"
            "与 feel 的区别：feel 是你此刻的情绪（会自然消退、对所有人一样），"
            "这个只对当前对话人生效，且不会自动消退——和好需要你主动清空。"
        )
        args: ClassVar = [
            FunctionCallingArg(
                name="tag",
                _type="str",
                description=(
                    "态度标签，简短短语，如「在闹别扭」「刚吵完架」「最近很黏人」。"
                    "传空字符串表示清除当前态度（和好、恢复正常）"
                ),
            ),
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            tag = str(data.data.get("tag") or "").strip()
            if len(tag) > 20:
                return "标签太长（最多 20 字），请用简短短语"
            user_info = get_user_info(data.sender_id)
            user_info.mood_tag = tag
            save_user_info(data.sender_id, user_info)
            if tag:
                return f"已记下你对这个人的态度：{tag}"
            return "已清除对这个人的态度，恢复正常"

    FunctionCallingManager.register(RelationSetFunctionCalling)

    class FeelFunctionCalling(FunctionCalling):
        function_name = "feel"
        scope = FunctionScope.CHAT
        returns_data = False
        parallel_safe = True
        description = (
            "表达你此刻的情绪，会持续影响接下来一段时间的语气。"
            "如被冒犯、被感动、被逗笑、觉得无聊。"
        )
        args: ClassVar = [
            FunctionCallingArg(
                name="mood",
                _type="float",
                description="心情变化，-1.0 到 1.0，负数变差、正数变好；不需要就省略",
            ),
            FunctionCallingArg(
                name="irritation",
                _type="float",
                description="烦躁增加量，0.0 到 1.0；被冒犯/被反复打扰时用，不需要就省略",
            ),
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            mood = data.data.get("mood")
            irritation = data.data.get("irritation")
            if mood is None and irritation is None:
                return "没有变化"
            try:
                state = feel(
                    mood=float(mood or 0.0),
                    irritation=float(irritation or 0.0),
                )
            except (TypeError, ValueError):
                return "情绪参数无效"
            log.debug(
                f"情绪变化 mood={state.mood:.2f} irritation={state.irritation:.2f}"
            )
            telemetry.feel(
                mood=state.mood, irritation=state.irritation, source="feel_tool"
            )
            return f"你现在的感受：{state.describe() or '很平静'}"

    FunctionCallingManager.register(FeelFunctionCalling)

    class SendFaceFunctionCalling(FunctionCalling):
        function_name = "face"
        aliases = ("send_face",)
        scope = FunctionScope.CHAT
        returns_data = False
        parallel_safe = False
        description = (
            "仅在表达自己的强烈情绪时发送表情。\n"
            "intent 写想表达的情绪；具体选哪个表情由聊天模型决定，"
            "不可用时不会发送。"
        )
        args: ClassVar = [
            FunctionCallingArg(
                name="intent", _type="str", description="为什么想发表情、想表达什么情绪"
            ),
            FunctionCallingArg(
                name="with_text",
                _type="bool",
                description="(可选)是否允许聊天模型附一句话，默认 false",
            ),
        ]

        @classmethod
        def is_available_for(cls, sender: ChatSender | None, user_id: str) -> bool:
            if sender is None:
                return False
            if isinstance(sender, OneBotChatSender):
                return True
            # 裸 ChatSender 只是抽象基类，不是任何真实通道。
            return type(sender) is not ChatSender and getattr(sender, "mtype", 1) == 0

        @staticmethod
        async def call(data: FunctionCallingData):
            # 没有 Chat 模型就无法从可用表情中挑选；按 3.5.7 直接不发送。
            # 正常链路由 Outbox 以 face job 交给 ChatModel.render。
            log.debug("face 未经过 Chat 渲染，已跳过（缺少可选表情）")
            return None

    FunctionCallingManager.register(SendFaceFunctionCalling)

    class RelationQueryFunctionCalling(FunctionCalling):
        function_name = "rel_get"
        aliases = ("relation_query",)
        scope = FunctionScope.CHAT
        description = (
            "查询你与某个人的关系，或列出你认识的人。\n"
            "提示词里只会给出当前对话人的关系，其他人都要在这里查。\n"
            "查不到就如实说不太记得，不要凭印象编一个关系出来。"
        )
        args: ClassVar[list[FunctionCallingArg]] = [
            FunctionCallingArg(
                name="name",
                _type="str",
                description="（可选）要查的人：QQ 号、名字或别名",
            ),
            FunctionCallingArg(
                name="master",
                _type="bool",
                description="（可选）查主人信息时置为 true",
            ),
            FunctionCallingArg(
                name="list_all",
                _type="bool",
                description="（可选）列名单时置为 true，按熟络度最多 10 位",
            ),
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            from .user import describe_masters, find_user, list_known_users

            keyword = str(data.data.get("name") or "").strip()
            wants_master = _as_bool(data.data.get("master"))
            wants_all = _as_bool(data.data.get("list_all"))
            if not (keyword or wants_master or wants_all):
                return (
                    "给一个参数：name 查某个人，master=true 查主人，"
                    "list_all=true 列名单（按熟络度最多 10 位）。"
                )

            # 给几个参数就返回几份结果，不要求「三选一」：一次问「我和主人熟吗，
            # 顺带列下我认识谁」本来就是两个问题，没必要逼它分两次调用。
            blocks = []
            if keyword:
                found = find_user(keyword)
                if found is None:
                    blocks.append(
                        f"你不太记得「{keyword}」这个人。可以如实说不认识，不要编。"
                    )
                else:
                    here = (
                        "（就是正在跟你说话的这位）"
                        if found["user_id"] == str(data.sender_id)
                        else ""
                    )
                    text = f"{found['user_id']}{here}：{found['relation']}"
                    if found["profile"]:
                        text += f"\n你对他的印象：{found['profile']}"
                    blocks.append(text)

            if wants_master:
                blocks.append(describe_masters())

            if wants_all:
                users = list_known_users()
                if not users:
                    blocks.append("你还没记住任何人。")
                else:
                    lines = ["你认识的人（按熟悉程度）："]
                    for u in users:
                        label = u["user_id"]
                        if u["nickname"]:
                            label += f"（{u['nickname']}）"
                        if u["alias"]:
                            label += "别名：" + "、".join(u["alias"])
                        if u["user_id"] == str(data.sender_id):
                            label += "（就是正在跟你说话的这位）"
                        lines.append(f"- {label}：{u['relation']}")
                    blocks.append("\n".join(lines))

            return "\n\n".join(blocks)

    FunctionCallingManager.register(RelationQueryFunctionCalling)

    class GetUserInfoFunctionCalling(FunctionCalling):
        function_name = "user"
        aliases = ("get_user_info",)
        scope = FunctionScope.CHAT
        description = (
            "查一个人的完整资料：QQ 号、昵称、别名、关系、好感度、熟络度、你对他的印象。\n"
            "查询串可以是 **QQ 号、名字或别名**，三者都认；"
            "查不到就如实说不记得，不要编。"
        )
        args: ClassVar[list[FunctionCallingArg]] = [
            FunctionCallingArg(
                name="query",
                _type="str",
                description="QQ 号 / 名字 / 别名任选其一",
            ),
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            from .user import find_user

            keyword = str(data.data.get("query") or "").strip()
            if not keyword:
                return "要给个 QQ 号、名字或别名才能查。"

            found = find_user(keyword)
            if found is None:
                return f"你不太记得「{keyword}」这个人。可以如实说不认识，不要编。"

            lines = [f"QQ {found['user_id']}"]
            if found["nickname"]:
                lines[0] += f"（{found['nickname']}）"
            if found["is_master"]:
                lines[0] += "——这是你主人"
            if found["alias"]:
                lines.append("别名：" + "、".join(found["alias"]))
            lines.append(f"关系：{found['relation']}")
            lines.append(f"好感度 {found['love']}，熟络度 {found['familiarity']:.2f}")
            if found["profile"]:
                lines.append(f"你对他的印象：{found['profile']}")
            return "\n".join(lines)

    FunctionCallingManager.register(GetUserInfoFunctionCalling)

    class UserAliasFunctionCalling(FunctionCalling):
        function_name = "alias"
        aliases = ("user_alias",)
        scope = FunctionScope.CHAT
        description = (
            "记下或忘掉一个人的别名（外号、习惯称呼）。\n"
            "什么时候用：有人说「叫我小 A」「他外号是 A 酱」，"
            "或者你发现大家老用另一个名字称呼某人时。\n"
            "先用 user 确认是哪个人，再记下来；记错了用 opt=remove 去掉。"
        )
        args: ClassVar[list[FunctionCallingArg]] = [
            FunctionCallingArg(
                name="query",
                _type="str",
                description="QQ 号 / 名字 / 已有别名，用来定位是哪个人",
            ),
            FunctionCallingArg(
                name="alias",
                _type="str",
                description="要记下或去掉的别名（不超过 20 字）",
            ),
            FunctionCallingArg(
                name="opt",
                _type="str",
                description="'add' 记下（默认），'remove' 去掉",
            ),
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            from .user import add_alias, find_user, remove_alias

            query = str(data.data.get("query") or "").strip()
            alias = str(data.data.get("alias") or "").strip()
            opt = str(data.data.get("opt") or "add").strip().lower()
            if not alias:
                return "要说明记哪个别名。"

            found = find_user(query)
            if found is None and query.isdigit():
                # 还没聊过的人也该能记别名：直接用 QQ 号定位，不要求先有档案。
                target = query
            elif found is None:
                return f"你不太记得「{query}」这个人，先确认是哪位再说。"
            else:
                target = found["user_id"]

            if opt == "remove":
                ok, message = remove_alias(target, alias)
            else:
                ok, message = add_alias(target, alias)
            return f"（{target}）{message}" if ok else message

    FunctionCallingManager.register(UserAliasFunctionCalling)

    class UserProfileGetFunctionCalling(FunctionCalling):
        function_name = "profile"
        aliases = ("user_profile",)
        scope = FunctionScope.CHAT
        description = "获取或修改指定用户的用户画像。用户画像是用于存储用户的个性化信息和偏好设置的文本数据，它可以包含用户的兴趣、行为模式、偏好设置等信息，用于在与用户交互时提供更个性化的体验。同时也可以用于存储你对用户的看法和评价，可以多次调用"
        args: ClassVar = [
            FunctionCallingArg(
                name="opt",
                _type="str",
                description="操作类型，'get'表示获取，'change'表示修改",
            ),
            FunctionCallingArg(name="user_id", _type="str", description="目标用户ID"),
            FunctionCallingArg(
                name="profile_change",
                _type="str",
                description="如何更改用户画像，除自己以外的人物全都使用用户ID，仅在change操作时需要",
            ),
        ]

        @classmethod
        def can_enable(cls) -> EnableCheck:
            if not llm_manager.has_type(ModelType.ATRI_PROFILE):
                return EnableCheck(
                    available=False, reason="未配置 atri-profile 类型模型"
                )
            return EnableCheck()

        @staticmethod
        async def call(data: FunctionCallingData):
            user_id = data.data.get("user_id")
            opt = data.data.get("opt")
            profile_change = data.data.get("profile_change")
            if opt == "get":
                return UserProfile.get_profile(user_id)
            elif opt == "change":
                log.info(f"用户 {user_id} 更改画像：{profile_change}")
                now_user_profile = await UserProfile.change_profile(
                    user_id, profile_change
                )
                return f"用户 {user_id} 画像更改成功: {now_user_profile}"

    FunctionCallingManager.register(UserProfileGetFunctionCalling)

    class ScheduleTaskFunctionCalling(FunctionCalling):
        function_name = "remind"
        aliases = ("schedule_task",)
        scope = FunctionScope.CHAT
        description = (
            "给自己安排一个定时任务：到点时会**重新唤起你自己的思考**，"
            "由那一刻的你来决定说什么。\n"
            "适用场景：答应别人「十分钟后提醒你」、约定「明早八点叫我」、"
            "或你想在某个时间点做点什么。\n"
            "message 写的是**提醒内容**（到点时会看到的提示），不是提前写死的台词——"
            "比如「提醒主人喝水」，而不是「主人该喝水了哦」。"
            "这样到点说话时你能结合当时的心情与上下文。\n"
            "任务会在机器人重启后自动恢复，所以答应了就不会丢。\n"
            "提醒：不要凭空给自己定一堆任务，只在确实答应或确实需要时用。"
        )
        args: ClassVar[list[FunctionCallingArg]] = [
            FunctionCallingArg(
                name="message",
                _type="str",
                description="到点时给**你自己**看的提醒内容，如「提醒主人喝水」"
                "「该说早安了」。不要写成要直接发出去的台词",
            ),
            FunctionCallingArg(
                name="minutes",
                _type="float",
                description="多少分钟后触发（用于一次性提醒，如 10 表示十分钟后）。"
                "与 hour 二选一",
            ),
            FunctionCallingArg(
                name="hour",
                _type="int",
                description="每天几点触发（24 小时制，0-23）。用于「每天早上八点」"
                "这类周期性约定。与 minutes 二选一",
            ),
            FunctionCallingArg(
                name="minute",
                _type="int",
                description="配合 hour 使用，几点几分，默认 0",
            ),
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            from . import task as task_mod

            message = str(data.data.get("message") or "").strip()
            if not message:
                return "缺少 message：要说的话不能为空"

            minutes = data.data.get("minutes")
            hour = data.data.get("hour")
            minute = data.data.get("minute")
            # 存**有效等级**（已应用个人 downgrade_to），不是原始 ctx.level：
            # 否则被降权的主人创建任务后，到点 from_chat_args 会按 is_master_user
            # 复核回 master，绕过降级。
            level = PermissionResolver.effective_level(data.permission).value

            try:
                if hour is not None:
                    h = int(hour)
                    m = int(minute or 0)
                    if not (0 <= h <= 23 and 0 <= m <= 59):
                        return "hour 需要在 0-23，minute 需要在 0-59"
                    task_id = await task_mod.create_recurring(
                        data.chat_id,
                        message,
                        kind="cron",
                        spec={"hour": h, "minute": m},
                        bot_id=data.bot_id,
                        user_id=data.sender_id,
                        level=level,
                        group_id=data.permission.group_id,
                    )
                    return f"已安排：每天 {h:02d}:{m:02d} 提醒你「{message}」（id: {task_id}）"

                if minutes is None:
                    return "需要给出 minutes（多少分钟后）或 hour（每天几点）"

                delay = float(minutes) * 60
                task_id = await task_mod.create_once(
                    data.chat_id,
                    message,
                    delay,
                    bot_id=data.bot_id,
                    user_id=data.sender_id,
                    level=level,
                    group_id=data.permission.group_id,
                )
                return f"已安排：{float(minutes):.0f} 分钟后提醒你「{message}」（id: {task_id}）"
            except ValueError as exc:
                return f"安排失败：{exc}"
            except Exception as exc:
                log.warning(f"安排定时任务失败：{exc}")
                return "安排失败，请稍后再试"

    FunctionCallingManager.register(ScheduleTaskFunctionCalling)

    class ListTasksFunctionCalling(FunctionCalling):
        function_name = "tasks"
        aliases = ("list_tasks",)
        scope = FunctionScope.CHAT
        description = "查看当前会话里你已经安排、但还没执行完的定时任务。"
        args: ClassVar[list[FunctionCallingArg]] = []

        @staticmethod
        async def call(data: FunctionCallingData):
            from . import task as task_mod

            tasks = await task_mod.list_tasks(data.chat_id, data.bot_id)
            # 普通用户只能看到自己委托的任务；管理 / 主人看整个会话。
            # 用求值后的有效等级，个人 downgrade_to 不能绕过这里。
            effective = PermissionResolver.check(
                "tasks", data.permission, record=False
            ).level
            if effective is PermissionLevel.USER:
                tasks = [
                    t
                    for t in tasks
                    if str(t.get("user_id") or "") == str(data.sender_id)
                ]
            if not tasks:
                return "这个会话目前没有定时任务"
            return "当前任务：\n" + "\n".join(task_mod.describe(t) for t in tasks)

    FunctionCallingManager.register(ListTasksFunctionCalling)

    class CancelTaskFunctionCalling(FunctionCalling):
        function_name = "untask"
        aliases = ("cancel_task",)
        scope = FunctionScope.CHAT
        description = "取消一个自己安排的定时任务。任务 id 可先用 tasks 查看。"
        args: ClassVar[list[FunctionCallingArg]] = [
            FunctionCallingArg(
                name="task_id", _type="str", description="要取消的任务 id"
            )
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            from . import task as task_mod

            task_id = str(data.data.get("task_id") or "").strip()
            if not task_id:
                return "缺少 task_id"
            if await task_mod.cancel(task_id, data.chat_id, data.bot_id):
                return f"已取消任务 {task_id}"
            return f"没找到任务 {task_id}（可能已执行完或不属于这个会话）"

    FunctionCallingManager.register(CancelTaskFunctionCalling)

    class GetServiceListFunctionCalling(FunctionCalling):
        function_name = "services"
        aliases = ("get_service_list",)
        scope = FunctionScope.BOT
        description = (
            "查询当前可用服务列表及服务分类。需要了解自己支持哪些功能服务时调用。"
        )
        args: ClassVar = []

        @staticmethod
        async def call(data: FunctionCallingData):
            from ATRI.system.help.data_source import Helper

            return Helper.get_text_list()

    FunctionCallingManager.register(GetServiceListFunctionCalling)

    class GetServiceInfoFunctionCalling(FunctionCalling):
        function_name = "service"
        aliases = ("get_service_info",)
        scope = FunctionScope.BOT
        description = "查询指定服务的说明、可用命令和启用状态。"
        args: ClassVar = [
            FunctionCallingArg(
                name="service", _type="str", description="要查询的服务名"
            )
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            from ATRI.system.help.data_source import Helper

            service = data.data.get("service", "")
            if not service:
                return "请提供服务名。"
            return Helper.service_info(service)

    FunctionCallingManager.register(GetServiceInfoFunctionCalling)

    class GetCommandInfoFunctionCalling(FunctionCalling):
        function_name = "command"
        aliases = ("get_command_info",)
        scope = FunctionScope.BOT
        description = "查询指定服务中某条命令的类型、说明和别名。"
        args: ClassVar = [
            FunctionCallingArg(
                name="service", _type="str", description="命令所属的服务名"
            ),
            FunctionCallingArg(
                name="command", _type="str", description="要查询的命令名"
            ),
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            from ATRI.system.help.data_source import Helper

            service = data.data.get("service", "")
            command = data.data.get("command", "")
            if not service or not command:
                return "请提供服务名和命令名。"
            return Helper.cmd_info(service, command)

    FunctionCallingManager.register(GetCommandInfoFunctionCalling)

    class SearchFunctionCalling(FunctionCalling):
        function_name = "search"
        scope = FunctionScope.CHAT
        description = "搜索网络上的信息，并到一个总结性回答"
        args: ClassVar = [
            FunctionCallingArg(name="query", _type="str", description="搜索关键词")
        ]

        @classmethod
        def can_enable(cls) -> EnableCheck:
            if not config.get("search.enable"):
                return EnableCheck(available=False, reason="未启用联网搜索")
            if not config.get("search.api_key"):
                return EnableCheck(available=False, reason="未配置搜索 API 密钥")
            return EnableCheck()

        @staticmethod
        async def call(data: FunctionCallingData):
            """
            搜索网络信息
            支持多种搜索引擎和网站
            """
            query = data.data.get("query", "")
            if not query:
                return "请提供搜索关键词"
            try:
                # 构建搜索 URL
                url = "https://api.tavily.com/search"
                log.debug(f"搜索：{url}")
                # 发送请求
                response = await request.post(
                    url,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {config.get('search.api_key')}",
                    },
                    json={
                        "query": query,
                        "include_answer": "advanced",
                        "search_depth": "basic",
                        "chunks_per_source": 1,
                        "country": "china",
                    },
                )
                if response.status_code == 200:
                    resp = response.json()
                    log.info(f"{query} 搜索结果：\n{resp['answer']}")
                    return resp["answer"]
                else:
                    error_msg = f"搜索失败，状态码：{response.status_code}"
                    log.warning(error_msg)
                    return error_msg
            except Exception as e:
                error_msg = f"搜索出错：{e}"
                log.error(error_msg)
                return error_msg

    FunctionCallingManager.register(SearchFunctionCalling)

    class MemoryChangeFunctionCalling(FunctionCalling):
        function_name = "remember"
        aliases = ("memory_change",)
        scope = FunctionScope.CHAT
        returns_data = False
        parallel_safe = True
        description = (
            f"更改你对用户的临时长期记忆，该记忆存在数量上限{USER_MEMORY_LIMIT}"
        )
        args: ClassVar = [
            FunctionCallingArg(
                name="mem",
                _type="str",
                description="新增记忆临时长期记忆,超出上限则先入先出,不要添加时间戳,直接输出记忆内容,可选",
            ),
            FunctionCallingArg(
                name="emotion",
                _type="float",
                description=(
                    "这条记忆对你的情绪冲击，-1.0 到 1.0。被冒犯/难过取负，"
                    "被夸/开心取正，普通信息接近 0。越强的事你记得越久，可选"
                ),
            ),
            FunctionCallingArg(
                name="del",
                _type="list[int]",
                description="需要删除的记忆列表索引,请积极删除无用与过期的记忆,可选",
            ),
        ]

        @classmethod
        def can_enable(cls) -> EnableCheck:
            if not llm_manager.has_type(ModelType.ATRI_EMBEDDING):
                return EnableCheck(
                    available=False, reason="未配置 atri-embedding 类型模型"
                )
            return EnableCheck()

        @staticmethod
        async def call(data: FunctionCallingData):
            # 处理记忆变更
            mem = data.data.get("mem")
            del_index = data.data.get("del")
            emotion = data.data.get("emotion")
            # 新增记忆
            user_info = get_user_info(data.sender_id)
            msg = []
            if mem:
                try:
                    value = float(emotion or 0.0)
                except (TypeError, ValueError):
                    value = 0.0
                stamped = f"{now().strftime('%Y年%m月%d日%a-%H时%M分')} {mem}"
                user_info.memory.append(tag_emotion(stamped, value))
                overflow = len(user_info.memory) - USER_MEMORY_LIMIT
                if overflow > 0:
                    del user_info.memory[:overflow]
                    msg.append(f"新增记忆成功（超出上限，已遗忘最早 {overflow} 条）")
                else:
                    msg.append("新增记忆成功")
                log.debug(
                    f"用户 {data.sender_id} 新增记忆：{now().strftime('%Y年%m月%d日%a-%H时%M分')} {mem} (情绪 {value:+.2f})"
                )
            if del_index:
                if type(del_index) is int:
                    del_index = [del_index]
                del_index.sort(reverse=True)
                for index in del_index:
                    try:
                        index: int
                        user_info.memory.pop(index)
                        log.debug(f"用户 {data.sender_id} 删除记忆：{index}")
                        msg.append(f"删除记忆：{index}")
                    except IndexError:
                        log.warning(f"用户 {data.sender_id} 删除记忆失败：{index}")
                        msg.append(f"删除记忆失败：{index}")
            save_user_info(data.sender_id, user_info)
            return msg

    FunctionCallingManager.register(MemoryChangeFunctionCalling)

    class SelfMemoryFunctionCalling(FunctionCalling):
        function_name = "self_note"
        aliases = ("self_memory",)
        scope = FunctionScope.CHAT
        returns_data = False
        parallel_safe = True
        description = (
            "记下你自己（亚托莉）的经历与感受，不是关于用户的资料。"
            f"上限 {SELF_MEMORY_LIMIT} 条，超出先入先出。"
            "用于保持你作为独立个体的连续性：今天发生了什么、你对谁有什么感觉、"
            "你答应了什么。只在确有值得留下的经历时调用，日常寒暄不要记。"
        )
        args: ClassVar = [
            FunctionCallingArg(
                name="mem",
                _type="str",
                description=(
                    "你自己的经历或感受，用第一人称简短短语，"
                    "如「今天被说成机器人，有点不服气」。不要写时间戳"
                ),
            ),
            FunctionCallingArg(
                name="emotion",
                _type="float",
                description=(
                    "这件事对你的情绪冲击，-1.0 到 1.0。委屈/难过取负，"
                    "开心/被感动取正。越强的事你记得越久，可选"
                ),
            ),
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            mem = data.data.get("mem")
            if not mem:
                return "没有内容，未记录"
            try:
                value = float(data.data.get("emotion") or 0.0)
            except (TypeError, ValueError):
                value = 0.0
            ok = add_self_memory(str(mem), emotion=value)
            if not ok:
                return "记录失败"
            log.debug(f"亚托莉自我记忆：{mem} (情绪 {value:+.2f})")
            return f"已记下（共上限 {SELF_MEMORY_LIMIT} 条）"

    FunctionCallingManager.register(SelfMemoryFunctionCalling)

    class GetMemoriesFunctionCalling(FunctionCalling):
        function_name = "recall"
        aliases = ("get_memories",)
        scope = FunctionScope.CHAT
        description = "根据语义检索长期记忆。仅在需要回忆相关历史事实时使用，由你自行决定查询数量和最低相似度。"
        args: ClassVar = [
            FunctionCallingArg(
                name="query", _type="str", description="要回忆的主题或事实"
            ),
            FunctionCallingArg(
                name="top_k",
                _type="int",
                description="返回记忆数量，建议 1 到 5，由你根据需要决定",
            ),
            FunctionCallingArg(
                name="min_similarity",
                _type="float",
                description="最低相似度，范围 0 到 1，由你根据需要决定",
            ),
        ]

        @classmethod
        def can_enable(cls) -> EnableCheck:
            if not llm_manager.has_type(ModelType.ATRI_EMBEDDING):
                return EnableCheck(
                    available=False, reason="未配置 atri-embedding 类型模型"
                )
            return EnableCheck()

        @staticmethod
        async def call(data: FunctionCallingData):
            query = data.data.get("query", "")
            top_k = data.data.get("top_k", 5)
            min_similarity = data.data.get("min_similarity")
            if not query:
                return []
            return await memory_manager.get_memories(
                query,
                int(top_k),
                min_similarity,
                chat_id=data.chat_id,
                subject=data.sender_id,
            )

    FunctionCallingManager.register(GetMemoriesFunctionCalling)

    class AccessMemoryFunctionCalling(FunctionCalling):
        function_name = "reinforce"
        aliases = ("access_memory",)
        scope = FunctionScope.CHAT
        description = "采用并强化一条已经检索到的长期记忆。只能采用检索结果中的记忆，并自行决定强化值。"
        args: ClassVar = [
            FunctionCallingArg(
                name="memory_id", _type="int", description="检索结果中的记忆 ID"
            ),
            FunctionCallingArg(
                name="reinforce",
                _type="float",
                description="重要度强化值，建议 0 到 0.2，由你根据本次使用价值决定",
            ),
        ]

        @classmethod
        def can_enable(cls) -> EnableCheck:
            if not llm_manager.has_type(ModelType.ATRI_EMBEDDING):
                return EnableCheck(
                    available=False, reason="未配置 atri-embedding 类型模型"
                )
            return EnableCheck()

        @staticmethod
        async def call(data: FunctionCallingData):
            from .memory.manage import memory_manager

            memory_id = data.data.get("memory_id")
            reinforce = data.data.get("reinforce", 0.1)
            if memory_id is None:
                return "请提供要采用的记忆 ID"
            return await memory_manager.access_memory(
                {"id": int(memory_id)}, float(reinforce)
            )

    FunctionCallingManager.register(AccessMemoryFunctionCalling)

    class ListSkillsFunctionCalling(FunctionCalling):
        function_name = "skills"
        aliases = ("list_skills",)
        scope = FunctionScope.CHAT
        prompt_hidden = True
        description = (
            "列出你当前可查阅的全部技能与说明。技能目录通常已经写在你的提示词里；"
            "只有提示词说「还有 N 个技能未列出」时才调用本功能查完整清单。"
        )
        args: ClassVar[list[FunctionCallingArg]] = []

        @staticmethod
        async def call(data: FunctionCallingData):
            from .skills import skill_manager

            skills = skill_manager.list_queryable()
            if not skills:
                return "当前没有可查询的技能"
            return "\n".join(
                f"{s.name}: [{s.source_label}] {s.description or '无说明'}"
                + (f"\n  何时读：{s.when_to_use}" if s.when_to_use else "")
                for s in sorted(
                    skills, key=lambda item: (-int(item.priority or 0), item.name)
                )
            )

    FunctionCallingManager.register(ListSkillsFunctionCalling)

    class GetSkillContentFunctionCalling(FunctionCalling):
        function_name = "skill"
        aliases = ("get_skill_content",)
        scope = FunctionScope.CHAT
        description = (
            "读取指定技能的正文。技能名来自提示词里的技能目录或 skills()。\n"
            "长技能优先用 section 按章节读；不要凭印象编造玩法。"
        )
        args: ClassVar[list[FunctionCallingArg]] = [
            FunctionCallingArg(
                name="name", _type="str", description="技能名，来自技能目录"
            ),
            FunctionCallingArg(
                name="section",
                _type="str",
                description="(可选)章节标题或片段，如「指令列表」；只看某一节时填",
            ),
            FunctionCallingArg(
                name="offset",
                _type="int",
                description="(可选)从第几个字符开始读，默认0",
            ),
            FunctionCallingArg(
                name="limit",
                _type="int",
                description="(可选)最多读多少字，默认4000",
            ),
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            from .skills import skill_manager

            name = str(data.data.get("name", "")).strip()
            if not name:
                return "请提供技能名"
            skill = skill_manager.get(name)
            if skill is None:
                available = (
                    "、".join(s.name for s in skill_manager.list_queryable()) or "无"
                )
                return f"技能 {name} 不存在；当前可用技能：{available}"
            if not skill.enabled:
                return f"技能 {name} 已被停用"
            if skill.embedded:
                return f"技能 {name} 已直接写入你的提示词，无需读取"
            section = str(data.data.get("section") or "").strip()
            offset = data.data.get("offset") or 0
            limit = data.data.get("limit") or 0
            try:
                offset = int(offset)
            except (TypeError, ValueError):
                offset = 0
            try:
                limit = int(limit)
            except (TypeError, ValueError):
                limit = 0
            text = skill_manager.read(
                skill, section=section, offset=offset, limit=limit
            )
            telemetry.record(
                "skill_read",
                name=skill.name,
                section=section or None,
                offset=offset,
                chars=len(text),
            )
            return text

    FunctionCallingManager.register(GetSkillContentFunctionCalling)

    class PlanFunctionCalling(FunctionCalling):
        function_name = "plan"
        scope = FunctionScope.CHAT
        returns_data = True
        parallel_safe = False
        description = (
            "查看或填入你今天的日程。\n"
            "action=show：读今天的完整安排（固定作息 + 已填入的活动）。\n"
            "action=fill：按 hint 用日程模型填入活动，会自动补「前往/回到」；"
            "新填入会覆盖旧填入，但不会覆盖睡觉、吃饭等固定作息。\n"
            "action=clear：只清空今天填入的活动，保留固定作息。"
        )
        args: ClassVar[list[FunctionCallingArg]] = [
            FunctionCallingArg(
                name="action",
                _type="str",
                description="show / fill / clear，默认 show",
            ),
            FunctionCallingArg(
                name="date",
                _type="str",
                description="(可选)日期 YYYY-MM-DD，默认今天（06:00 前算前一天）",
            ),
            FunctionCallingArg(
                name="start",
                _type="str",
                description="(可选)只替换这个时间点之后的旧安排，如 13:00",
            ),
            FunctionCallingArg(
                name="end",
                _type="str",
                description="(可选)只替换到这个时间点为止的旧安排",
            ),
            FunctionCallingArg(
                name="hint",
                _type="str",
                description="(fill) 你想怎么安排今天，例如「下午想去海边，晚饭前回家」",
            ),
            FunctionCallingArg(
                name="truncate_after",
                _type="bool",
                description="(fill) 为 true 时清掉新计划之后的旧填入条目",
            ),
            FunctionCallingArg(
                name="force",
                _type="bool",
                description="(fill) 仅主人可用：允许覆盖固定作息条目",
            ),
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            from .life import get_life_system

            life = get_life_system()
            action = str(data.data.get("action") or "show").strip().lower()
            date = str(data.data.get("date") or "").strip() or None
            if action == "show":
                entries = life.plan(date)
                if not entries:
                    return "今天还没有安排。"
                from .life import describe_entries

                return describe_entries(entries)

            if action == "clear":
                removed = life.clear(date)
                return (
                    "已清空今天填入的活动，固定作息保留。"
                    if removed
                    else "今天本来就没有填入的活动。"
                )

            if action != "fill":
                return f"未知的 action：{action}，可用 show / fill / clear"

            force = _as_bool(data.data.get("force"))
            if force and PermissionResolver.effective_level(
                data.permission
            ).value not in {"master", "system"}:
                return "只有主人可以覆盖固定作息"

            hint = str(data.data.get("hint") or "").strip()
            if not hint:
                return "请用 hint 说明你想怎么安排"

            from ..llm import ModelType, llm_manager
            from .life.routine import describe_gaps
            from .life.system import extract_json
            from .role import ATRI

            if not llm_manager.has_type(ModelType.ATRI_SCHEDULE):
                return "未配置 atri-schedule 类型模型，无法填入日程"
            routine = life.routine()
            state = life.location(date)
            current = life.schedule_summary(date)
            home = life.home_summary()
            prompt = (
                "为亚托莉填入今天的日程片段。\n"
                f"角色：{ATRI.get_role_prompt()}\n"
                f"固定作息（不可覆盖）：\n"
                + "\n".join(
                    f"- {slot.start.strftime('%H:%M')}-{slot.end.strftime('%H:%M')} "
                    f"{slot.place}：{slot.activity}"
                    for slot in routine.slots
                )
                + f"\n可用空档：{describe_gaps(routine) or '无'}\n"
                f"现在的位置：{state.describe()}\n"
                f"现有安排：\n{current or '无'}\n"
                f"家的位置与周边：{home or '无'}\n"
                f"她的想法：{hint}\n"
                '只输出 JSON：{"entries": [...]}。每条包含 start、end、kind（stay/travel）、'
                "activity；stay 用 place，travel 用 from/to。"
                "地点变化必须写 travel；出门了必须安排回家。最多 12 条，每条活动不超过 60 字。"
            )
            try:
                response = await llm_manager.call_agent(
                    ModelType.ATRI_SCHEDULE, prompt, "Agent-日程填入"
                )
                payload = extract_json(response.content)
            except Exception as exc:
                log.warning(f"日程填入模型调用失败：{exc}")
                return "日程模型没有给出可用的安排，已保留原日程"

            entries, issues = life.parse_entries(payload)
            if issues:
                return "日程格式有问题：" + "；".join(issues)
            result = life.fill_schedule(
                entries,
                date=date,
                start=str(data.data.get("start") or ""),
                end=str(data.data.get("end") or ""),
                truncate_after=_as_bool(data.data.get("truncate_after")),
                force=force,
            )
            if not result.ok:
                return f"日程没有更新：{result.message}"
            if not life.outfit(date).day and not life.outfit(date).night:
                await life.generate_outfit(
                    date=date,
                    activities=result.message,
                    place=life.location(date).place,
                )
            return result.message

    FunctionCallingManager.register(PlanFunctionCalling)

    class WearFunctionCalling(FunctionCalling):
        function_name = "wear"
        scope = FunctionScope.CHAT
        returns_data = True
        parallel_safe = False
        description = (
            "查看或更换你今天的穿搭。白天与晚上是两套：晚上是回家后的居家服/睡衣。"
            "action=show 读当前穿搭；generate 让穿搭模型按今天的活动生成；"
            "set 手动指定其中一套。"
        )
        args: ClassVar[list[FunctionCallingArg]] = [
            FunctionCallingArg(
                name="action",
                _type="str",
                description="show / generate / set，默认 show",
            ),
            FunctionCallingArg(
                name="period",
                _type="str",
                description="(set) day 或 night",
            ),
            FunctionCallingArg(
                name="text",
                _type="str",
                description="(set) 这套穿搭的内容，最多 120 字",
            ),
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            from .life import get_life_system

            life = get_life_system()
            action = str(data.data.get("action") or "show").strip().lower()
            if action == "show":
                return life.outfits.describe()
            if action == "generate":
                ok, message = await life.generate_outfit(
                    activities=life.schedule_summary(),
                    place=life.location().place,
                )
                return message
            if action == "set":
                period = str(data.data.get("period") or "day").strip().lower()
                if period not in {"day", "night"}:
                    return "period 只能是 day 或 night"
                text = str(data.data.get("text") or "").strip()
                if not text:
                    return "请提供要设置的穿搭内容"
                life.set_outfit(period, text)
                label = "白天" if period == "day" else "晚上"
                return f"{label}穿搭已更新：{text}"
            return f"未知的 action：{action}，可用 show / generate / set"

    FunctionCallingManager.register(WearFunctionCalling)

    class HomeFunctionCalling(FunctionCalling):
        function_name = "home"
        scope = FunctionScope.CHAT
        returns_data = True
        parallel_safe = False
        description = (
            "查看或修改你的家。家按章节保存（位置、外观、房间、周边、物品、衣柜、冰箱）。\n"
            "show 读整篇或某一节；edit 替换某一节；add 在某一节末尾追加；"
            "generate 让家模型扩写；每次写入都会自动备份。"
        )
        args: ClassVar[list[FunctionCallingArg]] = [
            FunctionCallingArg(
                name="action",
                _type="str",
                description="show / edit / add / generate，默认 show",
            ),
            FunctionCallingArg(
                name="section",
                _type="str",
                description="(show/edit/add) 章节名，如「客厅」「物品」",
            ),
            FunctionCallingArg(
                name="content",
                _type="str",
                description="(edit/add) 要写入或追加的内容",
            ),
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            from .life import get_life_system

            store = get_life_system().home()
            action = str(data.data.get("action") or "show").strip().lower()
            section = str(data.data.get("section") or "").strip()
            content = str(data.data.get("content") or "").strip()
            if action == "show":
                return store.show(section)
            if action == "edit":
                if not section or not content:
                    return "edit 需要 section 与 content"
                return store.edit(section, content)
            if action == "add":
                if not section or not content:
                    return "add 需要 section 与 content"
                return store.add(section, content)
            if action == "generate":
                ok, message = await store.generate()
                return message
            return f"未知的 action：{action}，可用 show / edit / add / generate"

    FunctionCallingManager.register(HomeFunctionCalling)

    class WhereFunctionCalling(FunctionCalling):
        function_name = "where"
        scope = FunctionScope.CHAT
        description = "查看你现在在哪里、正在做什么、什么时候离开。"
        args: ClassVar[list[FunctionCallingArg]] = []

        @staticmethod
        async def call(data: FunctionCallingData):
            from .life import get_life_system

            return get_life_system().location().describe()

    FunctionCallingManager.register(WhereFunctionCalling)

    class GoFunctionCalling(FunctionCalling):
        function_name = "go"
        scope = FunctionScope.CHAT
        returns_data = True
        parallel_safe = False
        description = (
            "现在就出发去某个地方。会截断当前安排，插入一段路程与目的地的停留；"
            "适合「主人现在叫我过去」「临时想出门」这种不等原计划的场景。"
        )
        args: ClassVar[list[FunctionCallingArg]] = [
            FunctionCallingArg(name="place", _type="str", description="目的地"),
            FunctionCallingArg(
                name="minutes",
                _type="int",
                description="(可选)路程分钟数，默认15，范围5-45",
            ),
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            from .life import get_life_system

            place = str(data.data.get("place") or "").strip()
            if not place:
                return "请提供目的地"
            try:
                minutes = int(data.data.get("minutes") or 0)
            except (TypeError, ValueError):
                minutes = 0
            result = get_life_system().go(place, minutes)
            return result.message if result.ok else f"没有出发：{result.message}"

    FunctionCallingManager.register(GoFunctionCalling)

    from .function_plugin.builtin import register_builtin_function_plugins

    register_builtin_function_plugins()
    _log_function_plugin_problems()

    AgentEventBus.publish_sync(event_type="register_FC", source="agent")


def _log_function_plugin_problems():
    """校验功能插件声明的成员是否真的存在（只记日志，不阻断加载）。"""
    from .function_plugin.manage import function_plugin_manager
    from .skills import skill_manager

    try:
        function_plugin_manager.log_member_problems(
            set(FunctionCallingManager.function_calling),
            {skill.name for skill in skill_manager.list_all()},
        )
    except Exception as e:
        log.warning(f"功能插件成员校验失败:{str_traceback(e)}")
