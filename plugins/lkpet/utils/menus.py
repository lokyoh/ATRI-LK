"""各指令的交互选项。

集中在一个文件里，好处是**改一处就能看全**：选项对应的指令必须是真实存在
的，散落在各 handler 里很容易写了却指向一条不存在的命令。

所有 command 都经 :func:`cmd` 生成，自动带上当前 ``command_start``——
部署把前缀配成 ``！`` 时，用户点下去的内容也跟着变。

## 优先内嵌，不要堆在底部

QQ 端的选项是 markdown 里的 ``<qqbot-cmd-input>``。如果统一追加到消息末尾，
用户得自己把「第 3 项」和「末尾第 3 个链接」对上号，而且一长就占掉半屏。

所以渲染正文时优先用 :func:`inline` 把命令贴在它描述的那一行上::

    🐾 野狼  体力 80%  <命令>

只有当正文确实没有可挂靠的行（纯提示语）时，才退回 :func:`tail`。
"""

from __future__ import annotations

from .reply import Choice, Menu, cmd_at
from .texts import command_start


def cmd(path: str) -> str:
    """把 ``宠物.状态`` 这类相对路径拼成完整指令。"""
    return f"{command_start()}{path}"


def inline(label: str, path: str) -> str:
    """内嵌到当前行的可点命令。

    :param path: 相对路径（``宠物.喂食``），自动补上 ``command_start``。

    **只在 QQ 端返回内容**：OneBot 没有 ``<qqbot-cmd-input>``，把转义后的标记
    塞进正文只会变成一串看不懂的字符。所以渲染函数可以放心地在每一行都调用
    它——非 QQ 端会自然退化成原本的纯文本。
    """
    from .reply import is_qq

    if not is_qq():
        return ""
    return cmd_at(label, cmd(path))


def tail(choices: list[Choice]) -> str:
    """兜底：把选项拼成一行追加到末尾。

    只在正文挂不上命令时用——正常应把 :func:`inline` 拼进对应那一行。
    """
    from .reply import inline_links

    links = inline_links(choices)
    return f"\n\n> {links}" if links else ""


# ---------------------------------------------------------------- 常用


def main_menu() -> list[Choice]:
    """宠物主菜单：最常用的一屏。"""
    return list(
        Menu()
        .add("状态", cmd("宠物.状态"))
        .add("喂食", cmd("宠物.喂食"))
        .add("玩耍", cmd("宠物.玩耍"))
        .add("技能", cmd("宠物.技能"))
        .add("商店", cmd("宠物.商店"))
        .add("肉鸽", cmd("宠物.肉鸽"))
    )


def status_menu() -> list[Choice]:
    """看完状态后最可能接着点的几个。"""
    return list(
        Menu()
        .add("喂食", cmd("宠物.喂食"), style=1)
        .add("玩耍", cmd("宠物.玩耍"), style=1)
        .add("技能", cmd("宠物.技能"), style=0)
        .add("商店", cmd("宠物.商店"), style=0)
    )


def create_menu() -> list[Choice]:
    return list(
        Menu().add("创建宠物", cmd("宠物.创建")).add("玩法说明", cmd("帮助"), style=0)
    )


def train_menu() -> list[Choice]:
    """训练之后：最可能接着再练一次，或看看成果。"""
    return list(
        Menu()
        .add("再训练", cmd("宠物.训练"))
        .add("状态", cmd("宠物.状态"), style=0)
        .add("肉鸽", cmd("宠物.肉鸽"), style=0)
    )


def action_menu() -> list[Choice]:
    """喂食/玩耍/洗澡之后的「再来一次」。"""
    return list(
        Menu()
        .add("状态", cmd("宠物.状态"))
        .add("玩耍", cmd("宠物.玩耍"))
        .add("休息", cmd("宠物.休息"), style=0)
    )


def talent_menu() -> list[Choice]:
    return list(
        Menu()
        .add("看技能", cmd("宠物.技能"), style=0)
        .add("看传记", cmd("宠物.传记"), style=0)
    )


def shop_overview_menu(categories: list[dict] | None = None) -> list[Choice]:
    """商店概览：直接给分类入口，省得手打编号。"""
    result: list[Choice] = []
    for cat in (categories or [])[:4]:
        order = cat.get("order")
        name = str(cat.get("name") or cat.get("id") or "")
        if order is None or not name:
            continue
        result.append(Choice(label=name, command=cmd(f"宠物.商店 {order}"), style=0))
    result.append(Choice(label="洗点", command=cmd("宠物.商店 洗点"), style=0))
    return result


def shop_item_menu(number: str, *, affordable: bool = True) -> list[Choice]:
    """商品详情页：买 / 返回。

    :param number: 商品的**展示编号字符串**（形如 ``1-3``），不是 int——
        编号带着分类前缀，转数字会指向别的商品。
    """
    result: list[Choice] = []
    if affordable:
        result.append(
            Choice(label="购买", command=cmd(f"宠物.商店 确认 {number}"), style=1)
        )
    result.append(Choice(label="返回", command=cmd("宠物.商店"), style=0))
    return result


def rogue_menu(*, running: bool) -> list[Choice]:
    """肉鸽：没开局给「开始」，局内给「继续 / 进度 / 撤退」。"""
    if not running:
        return list(
            Menu()
            .add("开始", cmd("宠物.肉鸽 开始"))
            .add("进度", cmd("宠物.肉鸽 进度"), style=0)
        )
    return list(
        Menu()
        .add("继续", cmd("宠物.肉鸽 继续"))
        .add("预计进度", cmd("宠物.肉鸽 进度"), style=0)
        .add("撤退", cmd("宠物.肉鸽 撤退"), style=0)
    )


def reincarnation_menu(*, ready: bool) -> list[Choice]:
    """轮回：条件够了才给「确认轮回」，否则引导去看差在哪。"""
    if ready:
        return list(
            Menu()
            .add("确认轮回", cmd("宠物.轮回 确认"))
            .add("再想想", cmd("宠物.状态"), style=0)
        )
    return list(
        Menu().add("查看条件", cmd("宠物.轮回")).add("商店", cmd("宠物.商店"), style=0)
    )


def log_menu() -> list[Choice]:
    return list(
        Menu().add("传记", cmd("宠物.传记")).add("状态", cmd("宠物.状态"), style=0)
    )


def bio_menu() -> list[Choice]:
    return list(
        Menu().add("日志", cmd("宠物.日志")).add("轮回", cmd("宠物.轮回"), style=0)
    )


def list_menu() -> list[Choice]:
    return list(Menu().add("我的状态", cmd("宠物.状态")))
