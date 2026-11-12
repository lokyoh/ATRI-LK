"""把肉鸽的当前状态翻译成交互按钮。

## 为什么要这层

肉鸽局内是靠**回复编号**推进的（``1. 战斗`` / ``2. 商店`` / …）。在 OneBot 端
手打数字还行，QQ 官方客户端的输入体验差得多：用户得先记住编号、再敲一条
``/宠物.肉鸽 1``，很容易打错或干脆不知道该按什么。

所以每一步都该直接给按钮：``战斗`` / ``事件`` / ``商店`` 点一下就是
``/宠物.肉鸽 1`` 这样的完整指令。这里负责把 ``FloorState`` 的选项结构
翻译成 :class:`ATRI.system.lkpet.utils.reply.Choice`。

## 只依赖「能算出来」的信息

各状态的可选项在 ``run_state`` 里已经是现成的：

- ``floor``：``options`` 列表（``battle`` / ``event`` / ``shop``）
- ``relic_choice``：``offer`` 遗物 ID 列表
- ``battle``：只有「迎战」（固定 1）
- ``shop``：``stock`` 商品
- ``event``：按选项数

这里不复制业务判断，只做「取选项 → 贴标签」。
"""

from __future__ import annotations

from typing import Any

from ..utils.reply import Choice

#: 肉鸽指令的完整前缀（相对路径，由 menus.cmd 拼 command_start）。
ROGUE_CMD = "宠物.肉鸽"


def line_link_builder():
    """给核心层用的「整行变可点链接」构造器；非 QQ 端返回 None。

    core 只负责说「这一行对应哪条指令」，长什么样由这里决定——
    和 lkbot 商品列表同样的思路：**整个商品名就是按钮**。
    """
    from . import reply

    if not reply.is_qq():
        return None

    from .menus import cmd as _cmd

    def build(text: str, command: str) -> str:
        return reply.cmd_at(text, _cmd(command))

    return build


def line_links():
    """``with`` 上下文：在 QQ 端启用行内链接。"""
    from ..core.roguelike._common import line_links as _line_links

    return _line_links(line_link_builder())


def numbered_options(enabled: bool):
    """转发核心层的开关：正文要不要列「1. 战斗 / 2. 事件」。

    QQ 端由底部按钮承载选项，正文不必重复；OneBot 端没有按钮，编号得留着。
    放在这里是为了让 handler 只依赖 ``rogue_actions`` 一个模块。
    """
    from ..core.roguelike._common import numbered_options as _numbered

    return _numbered(enabled)


#: 单次最多给几个选项按钮。QQ 每行最多 3 个、总行数多了会刷屏。
MAX_CHOICES = 6


def _cmd(action: str) -> str:
    from ..utils.menus import cmd

    return cmd(f"{ROGUE_CMD} {action}")


def _pick(text: str, index: int) -> Choice:
    """第 ``index`` 号选项的按钮（编号从 1 开始，与局内输入一致）。"""
    return Choice(label=text, command=_cmd(str(index)))


def _floor_choices(state: Any) -> list[Choice]:
    """楼层路线：把每个 ``options`` 元素贴成可读标签。"""
    from ..core.roguelike._common import _text

    labels = {
        "battle": _text("opt_battle", "战斗"),
        "event": _text("opt_event", "事件"),
        "shop": _text("opt_shop", "商店"),
    }
    choices: list[Choice] = []
    for index, option in enumerate(getattr(state, "options", []) or [], start=1):
        label = labels.get(str(option), str(option))
        # 精英层在标签上点明，否则玩家不知道这场打的是强化怪
        if getattr(state, "elite", False) and str(option) == "battle":
            label += "·精英"
        choices.append(_pick(label, index))
    return choices


def _relic_choices(state: Any) -> list[Choice]:
    """遗物三选一：用遗物名当按钮文字。"""
    from ..core.roguelike.relics import relic_specs

    specs = relic_specs()
    choices: list[Choice] = []
    for index, relic_id in enumerate(getattr(state, "offer", []) or [], start=1):
        spec = specs.get(str(relic_id)) or {}
        label = str(spec.get("name") or relic_id)
        choices.append(_pick(label, index))
    return choices


def _battle_choices() -> list[Choice]:
    """战斗只有一个入口。"""
    from ..core.roguelike._common import _text

    return [_pick(_text("opt_fight", "迎战"), 1)]


def _shop_choices(state: Any, run: Any) -> list[Choice]:
    """商店：每件商品一个按钮，外加「离开」。"""
    from ..core.roguelike._common import _text

    labels = _shop_item_labels(state, run)
    choices: list[Choice] = []
    for index, label in enumerate(labels, start=1):
        choices.append(_pick(label, index))
    choices.append(Choice(label=_text("opt_leave", "离开"), command=_cmd("0")))
    return choices


def _shop_item_labels(state: Any, run: Any) -> list[str]:
    """商店商品的显示名；spec 里没有就退回角标。"""
    from ..core.roguelike.relics import relic_specs

    specs = relic_specs()
    labels: list[str] = []
    for index, relic_id in enumerate(getattr(state, "stock", []) or [], start=1):
        spec = specs.get(str(relic_id)) or {}
        labels.append(str(spec.get("name") or f"商品{index}"))
    return labels


def _event_choices(state: Any) -> list[Choice]:
    """事件：按选项数给按钮，标签用「选项 N」。"""
    count = _event_option_count(state)
    return [_pick(f"选项{i}", i) for i in range(1, count + 1)]


def _event_option_count(state: Any) -> int:
    """事件有几个选项。

    事件定义在 yaml 里，正常会带 ``options``；取不到时退回 2——多数事件的
    选项数就是 2~3，给两个至少不会一个都不给。
    """
    for attr in ("options", "choices"):
        value = getattr(state, attr, None)
        if isinstance(value, list) and value:
            return len(value)
    try:
        from ..core.roguelike._common import _find_event

        spec = _find_event(str(getattr(state, "event_id", "")), _current_region())
        opts = (spec or {}).get("options") or []
        if isinstance(opts, list) and opts:
            return len(opts)
    except Exception:
        pass
    return 2


def _current_region() -> dict[str, Any]:
    """当前区域定义；拿不到就空 dict（``_find_event`` 能容忍）。"""
    return {}


def choices_for(run: Any, state: Any) -> list[Choice]:
    """按当前状态给出可点的选项列表；未知状态返回空。"""
    type_name = getattr(state, "type_name", "")
    if type_name == "floor":
        return _floor_choices(state)[:MAX_CHOICES]
    if type_name == "relic_choice":
        return _relic_choices(state)[:MAX_CHOICES]
    if type_name == "battle":
        return _battle_choices()
    if type_name == "shop":
        return _shop_choices(state, run)[:MAX_CHOICES]
    if type_name == "event":
        return _event_choices(state)[:MAX_CHOICES]
    # need_ap / 空状态：没有可点的，靠「进度」看情况
    return []


def choices_from_rogue(rogue: Any) -> list[Choice]:
    """从存档行直接算按钮；没有进行中的局时返回空。"""
    try:
        from ..core.roguelike.run_state import RogueRun, state_from_pending

        run = RogueRun.from_dict(rogue.run_state)
        if not run.pending:
            return []
        return choices_for(run, state_from_pending(run.pending))
    except Exception:
        return []
