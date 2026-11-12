"""同一份内容、两种排版：QQ 走 markdown 结构，OneBot 走纯文本。

## 为什么要有这一层

``reply.py`` 只解决「发什么消息段」（QQ 发 markdown 段、OneBot 发字符串），
但**正文内部**的排版还需要再分一次流：

* QQ 的 markdown 支持 ``## 标题``、``**加粗**``、``> 引用``，卡片靠它们分层；
* OneBot 会把上面这些标记**原样**显示成 ``##``、``**``，用户看到的是乱码。

所以在 formatters 里逐行写 ``if is_qq(): ... else: ...`` 会到处开花，还容易漏。
这里把「怎么强调、怎么分节」收成一组语义化方法，调用方只表达意图：

    s = style.current()
    s.heading("🐾 小豆 的状态")
    s.label("属性")
    s.quote("小豆开心地吃下了食物")

QQ 端得到美丽的分层，OneBot 端得到等价可读的纯文本，**文案只有一份**。

## 为什么分隔线用引用块画

QQ markdown 的「支持格式」清单里没有 ``---`` 水平线；直接写会变成一行可见的
``---`` 符号，比不画线还难看。``> ─────────`` 一定按引用块渲染，视觉上等价于
一条细分隔线，且零风险。

## 数值条

``▰▰▰▰▱▱ 62/100`` 是纯文本字符，两端都能显示，不需要按适配器分流——
所以 :func:`bar` 是模块级函数，不挂在 :class:`Style` 上。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 数值条的填充 / 空白字符。
BAR_FULL = "▰"
BAR_EMPTY = "▱"

#: 默认条宽。10 格在手机屏宽下不会换行，又足够看出比例。
BAR_WIDTH = 10


ICONS = {
    "pet": "🐾",
    "daily": "📅",
    "feed": "🍚",
    "play": "🎾",
    "bathe": "🛁",
    "rest": "😴",
    "train": "🏋",
    "skill": "📚",
    "talent": "✨",
    "record": "📖",
    "affection": "💗",
    "reincarnation": "🔄",
    "shop": "🏛",
    "respec": "♻",
    "success": "✅",
    "failure": "❌",
    "reward": "🎉",
    "battle": "⚔",
    "danger": "⚠",
    "gift": "🎁",
    "boss": "👑",
    "rng": "🎲",
    "mana": "🔵",
    "guard": "🛡",
    "unlock": "🔓",
    "codex": "📇",
    "relic": "🗡",
    "pack": "🐝",
    "defeat": "💀",
    "phase": "🌀",
    "enemy_skill": "📜",
    "trait": "🔹",
    "empower": "🔥",
    "heal": "💚",
    "pace": "⏱",
    "settle": "📊",
    "retreat": "🏳",
    "tower": "🗼",
    "region": "🗺",
    "ticket": "🎫",
    "dawn": "🌅",
    "hint": "💡",
    "note": "★",
    "beginner_pack": "🎒",
    "star": "🌟",
    "logs": "🗓",
    "rogue_shop": "🛒",
    "tag": "🏷",
    "title_badge": "🎖",
    "ready": "⚡",
    "heart": "❤",
    "battle_log": "🗒",
}
"""**一义一符**：每个符号只表达一个含义，同一含义只用这一个符号。

之所以要这张表而不是随手挑 emoji：``texts.yaml`` 一度出现过 50 种符号 / 89 处，
其中 ``📖`` 同时表示「新手引导」「学会技能」「传记」「战报」四种东西——
玩家没法从符号判断这一行讲的是什么，符号就退化成装饰。

改文案时从这里取符号（``ICONS["skill"]`` 之类），**不要**再临时加新的 emoji。
新增含义时先在这张表里登记，再改 YAML。

容易混的两对已拆开：``🏛`` 是**轮回商店**、``🛒`` 是**肉鸽遗物商店**；
``🏅`` 是成就点特权、``🎖`` 是称号。
"""


def icon(name: str, default: str = "") -> str:
    """取某个含义对应的符号；未登记时返回 ``default``（不抛错，文案优先）。"""
    return ICONS.get(name, default)


#: 标签列宽（按汉字计）。所有数值条共用这一列，条才会左右对齐。
LABEL_CELLS = 3

_FULLWIDE = "\u3000"
"""全角空格：与汉字等宽，用来把不足 3 字的标签补齐。"""


def pad_label(label: str) -> str:
    """把标签补到 :data:`LABEL_CELLS` 个字宽。

    「饱食」是 2 个字、「行动点」是 3 个字，直接拼条会让最后一条比上面几条
    缩进 1 个字宽（真机截图量出来是 14px）。补齐后所有条的起点落在同一列。
    """
    text = label or ""
    return text + _FULLWIDE * max(0, LABEL_CELLS - len(text))


def bar(value: int | float, maximum: int | float, width: int = BAR_WIDTH) -> str:
    """画一条 ``▰▰▰▰▱▱`` 进度条。

    非零值至少给一格：``5/1000`` 画成全空会让人以为数据没读到，而既然已经把
    精确数值写在旁边，宁可让条稍微「乐观」一点。
    """
    try:
        current = int(value)
    except (TypeError, ValueError):
        current = 0
    try:
        total = int(maximum)
    except (TypeError, ValueError):
        total = 0
    total = max(1, total)
    ratio = min(1.0, max(0.0, current / total))
    filled = round(ratio * max(1, width))
    if current > 0 and filled == 0:
        filled = 1
    filled = min(max(0, width), filled)
    return BAR_FULL * filled + BAR_EMPTY * (max(0, width) - filled)


def meter(
    label: str,
    value: int | float,
    maximum: int | float,
    *,
    suffix: str = "",
    width: int = BAR_WIDTH,
) -> str:
    """``饱食 ▰▰▰▰▰▰▱▱▱▱ 62/100`` —— 标签 + 条 + 精确值（可选后缀）。

    标签一律经 :func:`pad_label` 补齐，所以同一张卡里的条起点必然对齐。
    """
    return (
        f"{pad_label(label)} {bar(value, maximum, width)} "
        f"{int(value)}/{int(maximum)}{suffix}"
    )


@dataclass(frozen=True)
class Style:
    """一种排版能力。``markdown=True`` 时输出 QQ 的 markdown 结构。"""

    markdown: bool

    # ------------------------------------------------------------ 分节

    def heading(self, text: str) -> str:
        """卡片标题。QQ 用二级标题，OneBot 原样。"""
        return f"## {text}" if self.markdown else text

    def label(self, text: str) -> str:
        """小分节标题（状态 / 属性 / 成长）：QQ 加粗，OneBot 原样。"""
        return f"**{text}**" if self.markdown else text

    def hr(self) -> str:
        """分节线。QQ 用引用块画线，OneBot 直接画。"""
        return "> ─────────" if self.markdown else "─────────"

    # ------------------------------------------------------------ 行内

    def bold(self, text: str) -> str:
        """行内强调单个数值。"""
        return f"**{text}**" if self.markdown else text

    def quote(self, text: str) -> str:
        """引用一句叙述（互动结果、NPC 台词）：QQ 缩进成引用块。"""
        if not self.markdown:
            return text
        return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())

    def bullet(self, text: str) -> str:
        """列表项。QQ 用 ``-``，OneBot 用 ``·``（保持既有观感）。"""
        return f"- {text}" if self.markdown else f"· {text}"

    def note(self, text: str) -> str:
        """整段提示（多条命令并排的那种兜底文字）。"""
        return self.quote(text)


#: OneBot / 单测环境：一切结构化标记都不加。
PLAIN = Style(markdown=False)

#: QQ 官方适配器：标题、加粗、引用、分隔线全开。
MARKDOWN = Style(markdown=True)


def current() -> Style:
    """当前该用哪种排版。

    取不到事件（单测、定时任务）时退回 :data:`PLAIN`：纯文本最多只是不够好看，
    而把 ``##`` 写进 OneBot 消息是可见的故障。
    """
    try:
        from .reply import is_qq

        return MARKDOWN if is_qq() else PLAIN
    except Exception:  # pragma: no cover - 框架未就绪时不该让渲染崩掉
        return PLAIN
