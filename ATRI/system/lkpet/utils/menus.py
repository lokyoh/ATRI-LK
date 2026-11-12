"""各指令的交互选项。

集中在一个文件里，好处是**改一处就能看全**：选项对应的指令必须是真实存在
的，散落在各 handler 里很容易写了却指向一条不存在的命令。

所有 command 都经 :func:`cmd` 生成，自动带上当前 ``command_start``——部署把
前缀配成 ``！`` 时，用户点下去的内容也跟着变。

## 优先内嵌，不要堆在底部

QQ 端的选项是 markdown 里的 ``<qqbot-cmd-input>``。如果统一追加到消息末尾，
用户得自己把「第 3 项」和「末尾第 3 个链接」对上号，而且一长就占掉半屏。

所以渲染正文时优先用 :func:`inline` 把命令贴在它描述的那一行上::

    🐾 野狼  体力 80%  <命令>

只有当正文确实没有可挂靠的行（纯提示语）时，才退回 :func:`tail`。

## 底部按钮按「卡片类别」分组

同一个类别的卡片用同一套底部按钮（详见各函数文档）：

* :func:`care_menu` —— 照护类：状态 / 四种互动 / 训练 / 创建成功 / 轮回完成 / 聊天；
* :func:`detail_menu` —— 记录类：技能 / 天赋 / 传记 / 日志 / 好感度，自动去掉
  指向当前这张卡的那个按钮；
* 商店 / 肉鸽 / 轮回确认是**操作型**按钮（「这一步做什么」），保持自己的语义。
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


def inline_append(label: str, path: str) -> str:
    """把可点命令**接在已有文本后面**（QQ 端返回带前导空格的标记）。

    与 :func:`inline` 的区别只有一个空格，但不加这个空格时按钮会紧贴前一个
    ``数值``/``）」``——真机上渲染成「熟练度 1.0卸下」，看起来像个错别字。
    非 QQ 端返回空串（不吃掉任何字符），所以调用方可以无条件 ``+``。
    """
    link = inline(label, path)
    return f" {link}" if link else ""


def tail(choices: list[Choice]) -> str:
    """兜底：把选项拼成一行追加到末尾。

    只在正文挂不上命令时用——正常应把 :func:`inline` 拼进对应那一行。
    选项超过 :data:`~reply.MAX_INLINE_PER_LINE` 时会折成多行，每行都要补
    引用前缀，否则第二行起会左对齐、看起来像散落的标记。
    """
    from .reply import inline_links

    links = inline_links(choices)
    return f"\n\n> {links.replace(chr(10), chr(10) + '> ')}" if links else ""


# ---------------------------------------------------------------- 照护类


def care_menu() -> list[Choice]:
    """照护卡（状态 / 四种互动 / 训练 / 创建成功 / 轮回完成 / 聊天）底部的动作组。

    列的是「玩家看完这张卡最可能想干的一件照护工作」——喂食 / 玩耍 / 洗澡 /
    休息 / 训练，正好覆盖 ``daily.yaml`` 里那五个行动点消耗项。

    技能与商店属于**配置与购买**，不是照护动作，放进来只会把五行挤出两个与
    当下无关的入口；它们仍可从各自的指令进入。

    训练排在最后：它有饱食 / 心情 / 疲劳三道门槛，失败时会给出「先喂食 / 先玩耍
    / 先休息」的可点建议，放在照护动作之后正合适。
    """
    return list(
        Menu()
        .add("喂食", cmd("宠物.喂食"))
        .add("玩耍", cmd("宠物.玩耍"))
        .add("洗澡", cmd("宠物.洗澡"))
        .add("休息", cmd("宠物.休息"))
        .add("训练", cmd("宠物.训练"))
    )


def action_menu() -> list[Choice]:
    """四种互动（喂食 / 玩耍 / 洗澡 / 休息）回执底部的统一动作组。

    与 :func:`care_menu` 同一套：互动完最顺手的就是接着做另一项照护，
    所以直接复用，不再维护第二份「只有状态/玩耍/休息」的短名单——
    那份名单会让玩家喂完食想洗澡时只能手打。
    """
    return care_menu()


def train_menu() -> list[Choice]:
    """训练之后：最可能接着再练一次，或看看成果。"""
    return list(
        Menu()
        .add("再训练", cmd("宠物.训练"))
        .add("状态", cmd("宠物.状态"))
        .add("肉鸽", cmd("宠物.肉鸽"))
    )


# ---------------------------------------------------------------- 记录类


CARD_ENTRIES = (
    ("状态", "宠物.状态"),
    ("技能", "宠物.技能"),
    ("天赋", "宠物.天赋"),
    ("传记", "宠物.传记"),
    ("日志", "宠物.日志"),
)
"""配置 / 记录类卡片的互相入口。"""


def detail_menu(exclude: str | None = None) -> list[Choice]:
    """配置 / 记录类卡片（技能 / 天赋 / 传记 / 日志 / 好感度）底部的统一导航。

    这些卡看完之后玩家通常想「去看另一张卡」，而不是立刻喂食：技能看天赋、
    天赋看技能、传记看日志。所以它们共用同一份出口表，只是**去掉当前这张**
    ——在一张卡底下摆一个指向自己的按钮是最没用的按钮。

    ``exclude`` 取 ``状态`` / ``技能`` / ``天赋`` / ``传记`` / ``日志``；
    取别的值（例如好感度卡传 ``好感度``）等于不排除，给全部五项（会折成 4+1 两行）。

    **上限是 4**：``reply.inline_links`` 每行最多排 4 个按钮，正好是排除当前卡
    之后的项数；只有好感度那种不在表里的卡才会折成第二行。
    """
    entries = [item for item in CARD_ENTRIES if item[0] != exclude]
    return [Choice(label=label, command=cmd(path)) for label, path in entries]


def skill_menu() -> list[Choice]:
    """技能卡底部的出口：与其它记录卡同一套（见 :func:`detail_menu`）。

    技能正文里每个技能已自带「装备 / 卸下」，底部只放导航，不重复操作。
    """
    return detail_menu(exclude="技能")


def talent_menu() -> list[Choice]:
    """天赋卡底部：同上，去掉指向自己的「天赋」。"""
    return detail_menu(exclude="天赋")


def log_menu() -> list[Choice]:
    """日志卡底部：与其它记录卡同一套出口（见 :func:`detail_menu`）。"""
    return detail_menu(exclude="日志")


def bio_menu() -> list[Choice]:
    """传记卡底部：同上。"""
    return detail_menu(exclude="传记")


# ---------------------------------------------------------------- 操作类


def shop_overview_menu(categories: list[dict] | None = None) -> list[Choice]:
    """商店概览：直接给分类入口，省得手打编号。

    最多 4 个分类 + 洗点，正好是 ``reply.MAX_INLINE_PER_LINE`` 的 4 个——再多
    就会折成第二行，把「概览」挤成半屏按钮。分类超出 4 个时其余仍可用编号进入，
    正文里的分类行本身也已经是可点的。
    """
    result: list[Choice] = []
    for cat in (categories or [])[:4]:
        order = cat.get("order")
        name = str(cat.get("name") or cat.get("id") or "")
        if order is None or not name:
            continue
        result.append(Choice(label=name, command=cmd(f"宠物.商店 {order}")))
    result.append(Choice(label="洗点", command=cmd("宠物.商店 洗点")))
    return result


def shop_item_menu(number: str, *, affordable: bool = True) -> list[Choice]:
    """商品详情页：买 / 返回。

    :param number: 商品的**展示编号字符串**（形如 ``1-3``），不是 int——
        编号带着分类前缀，转数字会指向别的商品。
    """
    result: list[Choice] = []
    if affordable:
        result.append(Choice(label="购买", command=cmd(f"宠物.商店 确认 {number}")))
    result.append(Choice(label="返回", command=cmd("宠物.商店")))
    return result


def rogue_menu(*, running: bool) -> list[Choice]:
    """肉鸽：没开局给「开始」，局内给「继续 / 进度 / 撤退」。"""
    if not running:
        return list(
            Menu().add("开始", cmd("宠物.肉鸽 开始")).add("进度", cmd("宠物.肉鸽 进度"))
        )
    return list(
        Menu()
        .add("继续", cmd("宠物.肉鸽 继续"))
        .add("预计进度", cmd("宠物.肉鸽 进度"))
        .add("撤退", cmd("宠物.肉鸽 撤退"))
    )


def reincarnation_menu(*, ready: bool) -> list[Choice]:
    """轮回：条件够了才给「确认轮回」，否则引导去看差在哪。"""
    if ready:
        return list(
            Menu()
            .add("确认轮回", cmd("宠物.轮回 确认"))
            .add("再想想", cmd("宠物.状态"))
        )
    return list(Menu().add("查看条件", cmd("宠物.轮回")).add("商店", cmd("宠物.商店")))
