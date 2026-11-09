from nonebot.message import Matcher


class ChatSender:
    """发送通道。

    ``sent_count`` 记录本次链路**真的发出去了几条**。主动发言限流只认这个数——
    「决定要说」和「说出口了」是两件事，模型可能在思考后只调用
    ``stop_thinking`` 一句不说，那种情况不该占用发言配额。

    ``mtype`` 声明这个通道属于哪种消息协议，供 ``send_md_message`` 决定
    用图片还是 Markdown 卡片渲染：0 = OneBot（渲染成图片），1 = QQ。
    靠 isinstance 判断会漏掉新增的通道——``BotChatSender`` 就曾因此
    落到 else 分支，把 Markdown 源码直接发进群。
    """

    #: 0 = OneBot（md 渲染成图片），1 = QQ（发 Markdown 卡片）
    mtype = 0

    def __init__(self) -> None:
        self.sent_count = 0
        self.md_refs: list[tuple[str, str]] = []

    async def send(self, message):
        pass

    async def finish(self, message=None):
        pass


def bot_mtype(bot) -> int:
    """判断这个 bot 属于哪种协议：0 = OneBot，1 = QQ 官方。"""
    try:
        from nonebot.adapters.qq import Bot as QQBot

        if isinstance(bot, QQBot):
            return 1
    except Exception:
        pass
    return 0


async def send_to_chat(bot, chat_id, message) -> bool:
    """不经过 matcher，直接用 bot 往会话里发一条消息。

    主动开口与定时任务都没有 matcher（它们不是由用户消息触发的），
    只能走 bot 的原生 API。两种协议各有不同：

    - **OneBot**：``send_group_msg(group_id=...)``，群号是数字，
      非数字（QQ 官方 openid）原样传。
    - **QQ 官方**：``post_group_messages(group_openid=..., msg_type=0, content=...)``，
      用 openid 而不是数字群号。

    :param message: 文本，或含 @ 段的 ``Message``。**QQ 官方要 @ 人只能走
        消息对象**：``post_group_messages`` 的 ``content`` 是纯字符串，
        ``msg_type=0`` 下不认 ``<@openid>``；换成 ``Message`` 后由
        ``send_to_group`` 拆段，``mention_user`` 才能真正生效。
    :return: 是否发送成功。
    """
    is_segments = not isinstance(message, str) and message is not None
    text = "" if is_segments else str(message or "").strip()
    if not is_segments and not text:
        return False

    try:
        if bot_mtype(bot) == 1:
            if is_segments:
                await bot.send_to_group(group_openid=str(chat_id), message=message)
                return True
            await bot.post_group_messages(
                group_openid=str(chat_id), msg_type=0, content=text
            )
            return True
        from .initiative_start import _group_id

        await bot.send_group_msg(
            group_id=_group_id(chat_id), message=message if is_segments else text
        )
        return True
    except Exception as exc:
        from ATRI.log import log

        # 错误码只是线索，**不是判决**：同一个码在不同平台/版本下含义可能不同，
        # 拿它当「群没了」的证据，会把好好的群永久拉黑。所以再查一次群列表，
        # 只有平台自己都说「没这个群」时才标记失效。
        if not looks_like_gone(exc):
            log.warning(f"发送到会话 {chat_id} 失败：{exc}")
            return False
        alive = await verify_chat_alive(bot, chat_id)
        if alive is False:
            mark_chat_gone(chat_id)
            log.warning(f"发送到会话 {chat_id} 失败，群列表里也确实没有它：{exc}")
        elif alive is True:
            # 正是「群明明在，却发不进去」：这个 bot 不在那个群里（或已被踢），
            # 而会话是「bot + 群」的组合，换 bot 发等于乱发。
            log.warning(
                f"发送到会话 {chat_id} 失败，但群列表里还有它 —— "
                f"群没问题，是这个 bot 发不进这个群：{exc}"
            )
        else:
            log.warning(f"发送到会话 {chat_id} 失败（目标状态无法核实）：{exc}")
        return False


#: 已确认**确实**发不进去的会话。仅进程内记录，重启后重新核实。
#:
#: 依据必须来自「平台群列表里查不到这个群」，而**不是**某个错误码。错误码的
#: 语义由平台定义，拿它当「群没了」的证据会把正常的群永久拉黑——那比多发几条
#: 失败日志严重得多。
_GONE_CHATS: set[str] = set()


def _error_code(exc: BaseException) -> int | None:
    code = getattr(exc, "code", None)
    if code is None:
        info = getattr(exc, "info", None)
        if isinstance(info, dict):
            code = info.get("code")
    try:
        return int(code) if code is not None else None
    except (TypeError, ValueError):
        return None


def looks_like_gone(exc: BaseException) -> bool:
    """**仅作提示**：这个错误看起来像「目标不可达」。

    只用来决定日志措辞、以及是否值得再核实一次，**不能**单独据此判定会话
    失效——请用 :func:`verify_chat_alive` 确认。
    """
    if _error_code(exc) == 11255:
        return True
    message = str(exc)
    return any(
        hint in message
        for hint in (
            "用户/群已注销",
            "群不存在",
            "机器人已被移出",
            "群已解散",
            "not in group",
        )
    )


async def verify_chat_alive(bot, chat_id: str) -> bool | None:
    """查这个群是否还在当前 bot 的群列表里。

    :return: ``True`` 在群里、``False`` 确实不在、``None`` 查不了（无法判断）。
    """
    try:
        from nonebot.adapters.onebot.v11 import Bot as OneBot

        if not isinstance(bot, OneBot):
            return None
        groups = await bot.get_group_list()
    except Exception as exc:
        from ATRI.log import log

        log.debug(f"核实会话 {chat_id} 失败（无法判断，返回 None）：{exc}")
        return None
    return any(str(group.get("group_id", "")) == str(chat_id) for group in groups or [])


def mark_chat_gone(chat_id: str) -> None:
    """记下这个会话已不可用。"""
    _GONE_CHATS.add(str(chat_id))


def is_chat_gone(chat_id: str) -> bool:
    """这个会话是否已被判定为不可用。"""
    return str(chat_id) in _GONE_CHATS


def clear_chat_gone(chat_id: str | None = None) -> None:
    """清掉标记（会话恢复时调用；不传就全清）。"""
    if chat_id is None:
        _GONE_CHATS.clear()
        return
    _GONE_CHATS.discard(str(chat_id))


class OneBotChatSender(ChatSender):
    def __init__(self, matcher: Matcher):
        super().__init__()
        self.matcher: Matcher = matcher

    async def send(self, message):
        await self.matcher.send(message)
        self.sent_count += 1

    async def finish(self, message=None):
        if message is not None:
            self.sent_count += 1
        await self.matcher.finish(message)


class QQChatSender(ChatSender):
    mtype = 1

    def __init__(self, matcher: Matcher):
        super().__init__()
        self.matcher: Matcher = matcher

    async def send(self, message):
        await self.matcher.send(message)
        self.sent_count += 1

    async def finish(self, message=None):
        if message is not None:
            self.sent_count += 1
        await self.matcher.finish(message)


def _plain_text(message) -> str:
    """把 Message 里的文字取出来，供写历史用（丢掉 @、图片等段）。"""
    try:
        text = message.extract_plain_text()
    except Exception:
        text = ""
    return str(text or "").strip() or str(message or "").strip()


class BotChatSender(ChatSender):
    """直接拿 bot 往群里发消息，不经过 matcher。

    定时任务到点时没有 matcher（它不是由用户消息触发的），
    但 Agent 的思考链路需要一个发送通道，所以这里直接用 bot。

    ``sent`` 记录真正发出去的文本，供调用方写历史——
    合成上下文不参与 ``add_response`` 回写，需要这个列表补记。

    ``cleaner`` 是可选的清洗函数。模型偶尔会把「话」和「理由」一起写出来、
    或带上 Markdown 与说话人前缀。主动开口是「先捕获、清洗、再发」，
    这里传同一个清洗函数即可获得同样保护；返回空串则不发送。

    ``finish`` 与 ``send`` 等价：没有 matcher 就没有「结束事件响应器」的概念。
    """

    def __init__(self, bot, chat_id, cleaner=None):
        super().__init__()
        self.bot = bot
        self.chat_id = str(chat_id)
        self.sent: list[str] = []
        self.cleaner = cleaner
        self.mtype = bot_mtype(bot)

    async def send(self, message):
        if not isinstance(message, str) and message is not None:
            if not await send_to_chat(self.bot, self.chat_id, message):
                return
            self.sent_count += 1
            self.sent.append(_plain_text(message))
            return

        raw = message
        text = str(message or "").strip()
        if not text:
            return
        is_rich = text.startswith("[CQ:") or text.startswith("base64://")
        if self.cleaner is not None and not is_rich:
            text = str(self.cleaner(text) or "").strip()
            if not text:
                return
            raw = text
        if not await send_to_chat(self.bot, self.chat_id, raw):
            return
        self.sent_count += 1
        self.sent.append(text)

    async def finish(self, message=None):
        if message is not None:
            await self.send(message)
