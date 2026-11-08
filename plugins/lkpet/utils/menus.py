"""各指令的交互按钮。

集中在一个文件里，好处是**改一处就能看全**：按钮对应的指令必须是真实存在
的，散落在各 handler 里很容易写了按钮却指向一条不存在的命令。

所有 command 都经 :func:`cmd` 生成，自动带上当前 ``command_start``——
部署把前缀配成 ``！`` 时，按钮发出去的内容也跟着变。
"""

from __future__ import annotations

from .reply import Choice, Menu
from .texts import command_start


def cmd(path: str) -> str:
    """把 ``宠物.状态`` 这类相对路径拼成完整指令。"""
    return f"{command_start()}{path}"


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


def action_menu() -> list[Choice]:
    """喂食/玩耍/洗澡之后的「再来一次」。"""
    return list(
        Menu()
        .add("状态", cmd("宠物.状态"))
        .add("玩耍", cmd("宠物.玩耍"))
        .add("休息", cmd("宠物.休息"), style=0)
    )


def skill_menu(
    owned: list[str] | None = None, equipped: set[str] | None = None
) -> list[Choice]:
    """技能页：对未装备的技能给「装备」，对已装备的给「卸下」。"""
    equipped = equipped or set()
    result: list[Choice] = list(Menu().add("技能总览", cmd("宠物.技能")))
    for name in (owned or [])[:4]:
        if name in equipped:
            result.append(
                Choice(
                    label=f"卸下{name}",
                    command=cmd(f"宠物.技能 卸下 {name}"),
                    style=0,
                )
            )
        else:
            result.append(
                Choice(label=f"装备{name}", command=cmd(f"宠物.技能 装备 {name}"))
            )
    return result


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


def shop_category_menu(items: list[tuple[str, str]] | None = None) -> list[Choice]:
    """分类页：每件商品一个「查看」，编号直接用下标。"""
    result: list[Choice] = [Choice(label="返回", command=cmd("宠物.商店"), style=0)]
    for index, (_, name) in enumerate((items or [])[:4], start=1):
        result.insert(
            index - 1,
            Choice(label=str(name)[:6], command=cmd(f"宠物.商店 {index}"), style=0),
        )
    return result


def shop_item_menu(index: int, *, affordable: bool = True) -> list[Choice]:
    """商品详情页：买 / 返回。"""
    result: list[Choice] = []
    if affordable:
        result.append(
            Choice(label="购买", command=cmd(f"宠物.商店 确认 {index}"), style=1)
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
