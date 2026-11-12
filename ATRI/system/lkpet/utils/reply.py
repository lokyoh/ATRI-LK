"""统一的回复出口：QQ 端用 markdown + 内联命令，OneBot 端退回纯文本。

## 为什么这么设计

lkpet 的回复散在数十处 ``matcher.finish(...)`` 里，要同时满足三件事：

1. **QQ 适配器用 md**：QQ 官方机器人对纯文本很抠，长文本容易显示成
   「该消息类型暂不支持查看」；markdown 才是它的主场。
2. **交互点要能点**：QQ 的输入体验差，让用户手打 ``/宠物.商店 确认 1-3``
   很容易打错。这里用 markdown 里的 ``<qqbot-cmd-input>``——它在正文里就是
   一个可点的小按钮，点了等于用户发出那条指令。
3. **OneBot 端不能被拖坏**：``markdown`` 段在 OneBot 是非法的，发出去会报错。
   所以必须按适配器分流。

## 为什么不用键盘按钮，也不把命令堆在底部

QQ 的 ``keyboard`` 段（底部按钮）有**总量上限**（10 个），超了整条消息发不
出去；而且它和正文里的 ``<qqbot-cmd-input>`` 会重复显示同一批选项。

把一批命令统一追加到正文末尾同样不好：既占地方，又要用户自己把「第 3 项」和
「末尾第 3 个链接」对上号。所以主力做法是 :func:`cmd_at`——把命令**内嵌到它
描述的那一行**上；只有正文确实挂不上命令（纯提示语）时才退回末尾追加。

这里**不发 keyboard 段**。

## 事件从哪来

lkpet 的回复点不少落在只拿到 ``matcher`` 的辅助函数里。层层传 ``event`` 会把
整条调用链改一遍，而 nonebot 的 ``current_event`` ContextVar 在 matcher 的
执行上下文里始终有效——所以这里用它，调用方不必关心。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from nonebot.adapters.qq import MessageEvent as QQMessageEvent

from ATRI.log import log
from ATRI.message import QQMessage

#: QQ markdown 单条长度上限（官方限制 4000 字，留出命令区的余量）。
MD_MAX_CHARS = 3200

#: 一行里最多排几个内联命令——纯排版考虑，多了会挤成一片看不清。
MAX_INLINE_PER_LINE = 4

#: 内联命令标记的开头。判断「正文里是否已经有可点命令」时用它，
#: 而不是逐个比对拼接后的完整字符串（指令带 URL 编码，比不干净会漏判）。
QQ_CMD_MARK = "<qqbot-cmd-input"


@dataclass
class Choice:
    """一个交互选项。

    :param label: 链接上显示的字
    :param command: 点下去实际发出的指令（完整、可执行）
    :param enter: **预留，当前不生效**。见下方说明。
    :param style: 预留：内联命令没有样式，保留字段让调用方不必区分

    ``enter`` 曾经被当成「点一下直接发送 / 只填入输入框」的开关，但 QQ 官方
    协议里这两个能力是**两种不同的标记**：

    * ``<qqbot-cmd-input>`` —— 只把文本**插入输入框**，由用户自己确认发送；
    * ``<qqbot-cmd-enter>`` —— 点一下直接发送，但**群聊与文字子频道不支持**。

    本插件全部是群聊场景，所以实际只有前者可用，``enter`` 无论真假都走
    「插入输入框」。想「点一下就发出去」只能靠键盘按钮（``keyboard`` 段），
    而那条路有 10 个按钮的总量上限，正文一长就整条发不出去——本插件明确不走。

    字段保留是为了不破坏既有调用方与测试；新代码不要依赖它。
    """

    label: str
    command: str
    enter: bool = True
    style: int = 1


def current_event() -> Any:
    """当前正在处理的事件；取不到返回 None。"""
    try:
        from nonebot.matcher import current_event as _current_event

        return _current_event.get()
    except LookupError:
        return None
    except Exception:
        return None


def is_qq(event: Any = None) -> bool:
    """这个事件是不是来自 QQ 官方适配器。"""
    if event is None:
        event = current_event()
    return isinstance(event, QQMessageEvent)


def _clamp_markdown(text: str) -> str:
    """md 过长时截断并注明——超限整条会发不出去，不如发前半段。"""
    body = text or ""
    if len(body) <= MD_MAX_CHARS:
        return body
    return body[:MD_MAX_CHARS] + "\n\n> 内容过长，已省略后半部分"


def inline_links(choices: Iterable[Choice]) -> str:
    """把选项拼成可点的内联命令，每行最多 :data:`MAX_INLINE_PER_LINE` 个。

    用 ``<qqbot-cmd-input>``：QQ 客户端会把它渲染成一个小按钮，点一下就把
    ``command`` 插进输入框。相比 ``keyboard`` 段，它没有数量上限，也不会和正文
    重复。

    **只在真没法内嵌时用**（例如正文只是一句提示、没有可挂靠的行），否则优先
    用 :func:`cmd_at` 把命令贴到对应那一行上——底部堆一排同样占地方。

    必须**主动换行**：``<qqbot-cmd-input>`` 是不可折行的原子组件，一行塞五六个
    会横向撑出屏幕，用户得左右滑动才看得全。``MAX_INLINE_PER_LINE`` 就是为此
    存在的上限（此前它是个没被使用的常量）。
    """
    items = list(choices)
    if not items:
        return ""
    per_line = max(1, int(MAX_INLINE_PER_LINE))
    links = [QQMessage.cmd(c.command, c.label, reference=False) for c in items]
    return "\n".join(
        " ".join(links[start : start + per_line])
        for start in range(0, len(links), per_line)
    )


def cmd_at(label: str, command: str) -> str:
    """把一个选项做成**可内嵌**的行内命令。

    用法是在渲染正文时直接把它拼进对应那一行::

        1. 战斗：野狼 <cmd_at("打", "/宠物.肉鸽 1")>

    这样命令就长在它描述的那一行上，而不是另起一段堆在底部——后者既占地方，
    又需要用户自己把「第 3 项」和「底部第 3 个链接」对上号。
    """
    return QQMessage.cmd(command, label, reference=False)


def md_with_choices(text: str, choices: Iterable[Choice]) -> str:
    """markdown 正文；仅在正文本身挂不上命令时才把命令追加到末尾。

    优先用 :func:`cmd_at` 把命令内嵌进对应的行。这个函数是给「正文只是一句
    提示、没有可挂靠的行」的情形兜底，不要当成默认做法。

    **正文里已经内嵌过命令就不再追加**：肉鸽的楼层/事件/商店渲染会把每一条
    选项做成整行链接，渲染完成后正文里已经有一批 ``<qqbot-cmd-input>``；
    此时再把同一批 ``choices`` 贴到末尾，等于同一件事在一屏里出现两遍，
    还要用户去末尾重找一遍。
    """
    body = _clamp_markdown(text)
    if QQ_CMD_MARK in body:
        return body
    links = inline_links(choices)
    if not links:
        return body
    # 多行按钮时每行都要带引用前缀，否则第二行起会左对齐、看起来像散落的标记。
    return f"{body}\n\n> " + links.replace("\n", "\n> ")


def plain_with_choices(text: str, choices: Iterable[Choice]) -> str:
    """OneBot 端的降级：把选项写成「指令清单」，用户复制即可。"""
    body = text or ""
    choices = list(choices)
    if not choices:
        return body
    lines = [body, "", "可选操作："]
    for choice in choices:
        lines.append(f"- {choice.label}：{choice.command}")
    return "\n".join(lines)


def render(event: Any, text: str, choices: Iterable[Choice] | None = None):
    """渲染成该适配器能发的消息。

    QQ 端只发一条 markdown（内含内联命令），**不附带 keyboard 段**。
    其他端返回纯文本。

    :return: QQ 端返回 ``Message``；其他端返回 ``str``。
    """
    if event is None:
        event = current_event()
    choices = list(choices or [])
    if not is_qq(event):
        return plain_with_choices(text, choices)

    from nonebot.adapters.qq import Message as QQMsg

    return QQMsg(QQMessage.md_msg(md_with_choices(text, choices)))


def safe_render(event: Any, text: str, choices: Iterable[Choice] | None = None):
    """``render`` 的兜底版：渲染失败也要把纯文本发出去。

    回复发不出去比格式难看严重得多，所以这里降级成纯文本。
    """
    try:
        return render(event, text, choices)
    except Exception as exc:
        log.warning(f"渲染 QQ 消息失败，降级为纯文本：{exc}")
        return plain_with_choices(text, list(choices or []))


async def send(matcher, text: str, choices: Iterable[Choice] | None = None):
    """发一条回复（不结束 matcher）。"""
    await matcher.send(safe_render(None, text, choices))


async def finish(matcher, text: str, choices: Iterable[Choice] | None = None):
    """发一条回复并结束 matcher。所有 ``matcher.finish`` 都应换成这个。"""
    await matcher.finish(safe_render(None, text, choices))


# ---------------------------------------------------------------- 便捷构造


def choice(label: str, command: str, *, enter: bool = True, style: int = 1) -> Choice:
    """构造一个 ``Choice`` 的简写。"""
    return Choice(label=label, command=command, enter=enter, style=style)


@dataclass
class Menu:
    """一组常用选项，便于各指令复用。"""

    choices: list[Choice] = field(default_factory=list)

    def add(self, label: str, command: str, **kwargs) -> Menu:
        self.choices.append(choice(label, command, **kwargs))
        return self

    def __iter__(self):
        return iter(self.choices)

    def __len__(self) -> int:
        return len(self.choices)
