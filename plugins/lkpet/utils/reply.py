"""统一的回复出口：QQ 端一律 markdown + 按钮，OneBot 端退回纯文本。

## 为什么要单独一层

lkpet 的回复散在数十处 ``matcher.finish(...)`` 里。要同时满足三件事：

1. **QQ 适配器用 md**：QQ 官方机器人对纯文本很抠，长文本容易显示成
   「该消息类型暂不支持查看」；markdown 才是它的主场。
2. **交互点用按钮**：QQ 的输入体验差，让用户手打 ``/宠物.商店 确认 1-3``
   很容易打错。按钮点一下就是把整条指令发出去。
3. **OneBot 端不能被拖坏**：QQ 官方的 ``markdown`` / ``keyboard`` 段在
   OneBot 是非法的，发出去会报错。所以必须按适配器分流。

所以所有回复都从这里出：``finish()`` 负责渲染并结束 matcher。

## 按钮怎么发

QQ 的按钮靠 markdown 里的 ``<qqbot-cmd-input>`` 触发，点一下等价于用户发出
``text`` 那段内容。``ATRI.message.QQMessage.cmd`` 已经做了 URL 编码，这里只
负责把按钮行拼到正文末尾，并给出 ``keyboard`` 段。

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
from nonebot.adapters.qq.models import (
    Action,
    Button,
    InlineKeyboard,
    InlineKeyboardRow,
    MessageKeyboard,
    Permission,
    RenderData,
)

from ATRI.log import log
from ATRI.message import QQMessage

#: QQ markdown 单条长度上限（官方限制 4000 字，留出按钮区的余量）。
MD_MAX_CHARS = 3200

#: 每行最多几个按钮（QQ 官方限制 3 个）。
MAX_BUTTONS_PER_ROW = 3


@dataclass
class Choice:
    """一个交互选项。

    :param label: 按钮上显示的字
    :param command: 点下去实际发出的指令（完整、可执行）
    :param enter: 是否直接发送（True）还是填进输入框让用户改（False）
    :param style: 0 灰、1 蓝（主操作）
    """

    label: str
    command: str
    enter: bool = True
    style: int = 1

    def to_button(self, index: int) -> Button:
        return Button(
            id=f"btn_{index}",
            render_data=RenderData(label=self.label, style=self.style),
            action=Action(
                type=2,
                permission=Permission(type=2),
                data=self.command,
                enter=self.enter,
            ),
        )


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


def md_with_choices(text: str, choices: Iterable[Choice]) -> str:
    """把选项拼进 markdown。

    按钮本身由 ``keyboard`` 段承载，但 QQ 客户端并不保证渲染它——所以正文里
    再写一行 ``<qqbot-cmd-input>``，两种渲染任一生效都能点。
    """
    body = _clamp_markdown(text)
    choices = list(choices)
    if not choices:
        return body
    links = " ".join(
        QQMessage.cmd(c.command, c.label, reference=False) for c in choices
    )
    return f"{body}\n\n> {links}"


def plain_with_choices(text: str, choices: Iterable[Choice]) -> str:
    """OneBot 端的降级：按钮变成「指令清单」，用户复制即可。"""
    body = text or ""
    choices = list(choices)
    if not choices:
        return body
    lines = [body, "", "可选操作："]
    for choice in choices:
        lines.append(f"- {choice.label}：{choice.command}")
    return "\n".join(lines)


def keyboard_rows(
    choices: Iterable[Choice], *, per_row: int = 2
) -> InlineKeyboard | None:
    """构造 QQ 的键盘结构；没有选项时返回 None（不附带键盘段）。"""
    choices = list(choices)
    if not choices:
        return None
    per_row = max(1, min(MAX_BUTTONS_PER_ROW, per_row))
    buttons = [choice.to_button(index) for index, choice in enumerate(choices)]
    rows = [
        InlineKeyboardRow(buttons=buttons[i : i + per_row])
        for i in range(0, len(buttons), per_row)
    ]
    return InlineKeyboard(rows=rows)


def render(event: Any, text: str, choices: Iterable[Choice] | None = None):
    """渲染成该适配器能发的消息。

    :return: QQ 端返回 ``Message``（含 keyboard 段）；其他端返回纯文本 ``str``。
    """
    if event is None:
        event = current_event()
    choices = list(choices or [])
    if not is_qq(event):
        return plain_with_choices(text, choices)

    from nonebot.adapters.qq import Message as QQMsg

    message = QQMsg(QQMessage.md_msg(md_with_choices(text, choices)))
    rows = keyboard_rows(choices)
    if rows is not None:
        message.append(QQMessage.keyboard(MessageKeyboard(content=rows)))
    return message


def safe_render(event: Any, text: str, choices: Iterable[Choice] | None = None):
    """``render`` 的兜底版：渲染失败也要把纯文本发出去。

    QQ 段构造涉及 pydantic 模型，参数不合法会抛异常。回复发不出去比格式难看
    严重得多，所以这里降级成纯文本。
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
