"""轮回商店：目录解析、等级定价与加成重算。

本模块**只做机制，不含内容**。商品的名称、价格、上限、效果全部来自
``res/data/lkpet/config/reincarnation.yaml`` 的 ``shop`` 块；该块不存在时
所有函数都退化为空结果，插件其余部分照常工作。

三种商品形态
------------
``level: infinite``（默认）
    无限等级，每级按固定比例涨价。用于"百分比成长"的永久属性——
    玩家的长期目标是**边际收益递减的钱坑**，而不是"买满就毕业"。

``level: capped`` + ``max_level``
    有限等级，等级到顶即封顶。适合"一次买满就永久生效"的结构性解锁。

``consumable: true``
    买完立刻生效、不涨等级（洗点券、入场券）。

定价写法
--------
``cost`` 支持三种写法::

    cost: 5000                      # 每级同价
    cost: [120, 250, 420]           # 逐级定价（长度须等于 max_level）
    cost: {start: 5000, mult: 1.5}  # 几何涨价：第 n 级 = start × mult^(n-1)

几何涨价是无限等级的唯一合理写法——列表无法表达无限级。
"""

import math
from typing import Any

PERK_EFFECT_KEYS = frozenset(
    {
        "exp_bonus",
        "atk_bonus",
        "def_bonus",
        "hp_bonus",
        "spd_bonus",
        "mp_bonus",
        "start_level_bonus",
        # 百分比成长（无限等级用）：乘区，0.01 = +1%
        "atk_pct",
        "def_pct",
        "hp_pct",
        "spd_pct",
        "mp_pct",
        "exp_pct",
    }
)
"""本模块认识的 ``effect`` 键白名单。

* ``*_bonus``：**平值**加成，直接加到属性上（``start_level_bonus`` 特殊，
  加的是新世代的起始等级）。
* ``*_pct``：**百分比**加成，作为乘区叠在基础值上（``0.01`` = +1%）。
  与平值互不干扰，两者可以同时存在。

新增效果键必须同时改这里**和**它的消费者，否则会在配置校验里被拒
（见 :func:`validate_catalog`）。
"""

LEVEL_INFINITE = "infinite"
LEVEL_CAPPED = "capped"

_CONSUMABLE = "consumable"
"""``spec_level_cap`` 用 0 表示"没有等级概念"。"""


def _load_reincarnation_config() -> dict[str, Any]:
    from ..config_loader import load_config

    return load_config("reincarnation")


def shop_config(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """``reincarnation.yaml`` 的 ``shop`` 块；缺失时返回空 dict。"""
    base = cfg if cfg is not None else _load_reincarnation_config()
    node = base.get("shop") if isinstance(base, dict) else None
    return node if isinstance(node, dict) else {}


def categories(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """商店分类，按 ``order`` 升序（缺 ``order`` 的排在最后，保持配置顺序）。"""
    raw = shop_config(cfg).get("categories") or []
    items = [dict(item) for item in raw if isinstance(item, dict)]
    return sorted(items, key=lambda item: int(item.get("order", 999)))


def catalog(cfg: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """商品目录 ``{item_id: spec}``；``shop.items`` 缺失时返回 ``{}``。

    每个 spec 会被补上 ``id`` 字段，避免调用方到处做 ``(item_id, spec)`` 配对。
    """
    raw = shop_config(cfg).get("items") or {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item_id, spec in raw.items():
        if isinstance(spec, dict):
            merged = dict(spec)
            merged["id"] = str(item_id)
            result[str(item_id)] = merged
    return result


# ---------------------------------------------------------------------------
# 等级形态
# ---------------------------------------------------------------------------


def is_consumable(spec: dict[str, Any]) -> bool:
    """消耗品：买完立刻生效，不产生等级。"""
    return bool(spec.get("consumable"))


def level_mode(spec: dict[str, Any]) -> str:
    """``infinite`` / ``capped`` / ``consumable``。"""
    if is_consumable(spec):
        return _CONSUMABLE
    mode = str(spec.get("level", LEVEL_INFINITE) or LEVEL_INFINITE).strip().lower()
    return LEVEL_CAPPED if mode == LEVEL_CAPPED else LEVEL_INFINITE


def spec_level_cap(spec: dict[str, Any], levels: dict[str, int] | None = None) -> int:
    """某商品的等级上限。

    * ``consumable`` → ``0``（没有等级概念）
    * ``capped`` → ``max_level``（必填，缺省 1）
    * ``infinite`` → **已买等级 + 1**：永远比现状多一级可买。

    无限等级没有"绝对上限"，所以传 ``levels`` 才能得到有意义的"下一级"。
    不传时返回 1，仅用于"至少还有一级可买"的判断。
    """
    mode = level_mode(spec)
    if mode == _CONSUMABLE:
        return 0
    if mode == LEVEL_CAPPED:
        try:
            return max(1, int(spec.get("max_level", 1)))
        except (TypeError, ValueError):
            return 1
    owned = 0
    if levels:
        try:
            owned = max(0, int(levels.get(str(spec.get("id", "")), 0)))
        except (TypeError, ValueError):
            owned = 0
    return owned + 1


def has_next_level(
    spec: dict[str, Any],
    owned: int,
) -> bool:
    """还能不能再买一级（``capped`` 到顶后为 ``False``，``infinite`` 恒 ``True``）。"""
    if is_consumable(spec):
        return True
    if level_mode(spec) == LEVEL_CAPPED:
        try:
            return owned < max(1, int(spec.get("max_level", 1)))
        except (TypeError, ValueError):
            return owned < 1
    return True


# ---------------------------------------------------------------------------
# 定价
# ---------------------------------------------------------------------------


def price_for(
    spec: dict[str, Any],
    level: int,
    cfg: dict[str, Any] | None = None,
) -> int:
    """第 ``level`` 级的价格（``level`` 从 0 起，即买第 1 级传 0）。

    三种 ``cost`` 写法见模块文档。几何涨价按 ``start × mult ** level`` 计算，
    取整用 ``round``（不是 ``int``）：1.5 倍连乘时 ``int`` 会系统性偏低。
    """
    raw = spec.get("cost")
    index = max(0, int(level))

    if isinstance(raw, int | float):
        return max(0, int(raw))

    if isinstance(raw, dict):
        start = raw.get("start", 0)
        mult = raw.get("mult", 1.0)
        try:
            base = float(start)
            ratio = float(mult)
        except (TypeError, ValueError):
            return 0
        if base <= 0 or ratio <= 0:
            return 0
        return max(0, round(base * (ratio**index)))

    if isinstance(raw, list | tuple) and raw:
        pos = min(index, len(raw) - 1)
        try:
            return max(0, int(raw[pos]))
        except (TypeError, ValueError):
            return 0

    return 0


def total_cost(
    spec: dict[str, Any],
    levels: int,
    cfg: dict[str, Any] | None = None,
) -> int:
    """买 ``levels`` 级共需多少点（洗点退款按这个口径算）。"""
    return sum(price_for(spec, i, cfg) for i in range(max(0, int(levels))))


def preview_prices(
    spec: dict[str, Any],
    owned: int,
    count: int = 5,
    cfg: dict[str, Any] | None = None,
) -> list[int]:
    """从 ``owned`` 之后的若干级价格，用于展示"越买越贵"。"""
    return [price_for(spec, owned + i, cfg) for i in range(max(0, int(count)))]


# ---------------------------------------------------------------------------
# 效果
# ---------------------------------------------------------------------------


def total_effect(
    spec: dict[str, Any],
    levels: int,
    cfg: dict[str, Any] | None = None,
) -> dict[str, float]:
    """``levels`` 级累计效果 = ``effect × levels``（每级效果按配置叠加）。"""
    effect = spec.get("effect") or {}
    if not isinstance(effect, dict) or levels <= 0:
        return {}
    result: dict[str, float] = {}
    for key, value in effect.items():
        if not isinstance(value, int | float):
            continue
        result[str(key)] = float(value) * levels
    return result


def recompute_bonuses(
    levels: dict[str, int],
    cfg: dict[str, Any] | None = None,
) -> dict[str, int | float]:
    """从 ``{商品 id: 等级}`` 重算出完整的 ``permanent_bonuses``。

    这是 ``permanent_bonuses`` 的**唯一推导入口**：它是缓存而不是原始数据，
    所以任何改动（买、洗点、改配置）都走"重算"而不是"累加"。好处是
    改配置数值后玩家下次上线自动对齐，不需要再写一次数据迁移；
    重复调用也不会有副作用（幂等）。

    **配置里已不存在的商品 id 会被静默忽略**——下线一件商品不该让
    玩家的整份加成消失，而应只是那一项不再贡献。
    """
    specs = catalog(cfg)
    if not specs or not levels:
        return {}
    bonus: dict[str, int | float] = {}
    for item_id, owned in levels.items():
        spec = specs.get(str(item_id))
        if spec is None:
            continue
        try:
            count = int(owned)
        except (TypeError, ValueError):
            continue
        if count <= 0:
            continue
        # ``capped`` 商品调低上限后，已买等级按新上限截断；``infinite`` 不截断。
        if level_mode(spec) == LEVEL_CAPPED:
            try:
                count = min(count, max(1, int(spec.get("max_level", 1))))
            except (TypeError, ValueError):
                count = min(count, 1)
        for key, value in total_effect(spec, count, cfg).items():
            bonus[key] = bonus.get(key, 0) + value
    # 平值类保留 int、百分比类保留 float，与配置里的字面量类型一致。
    result: dict[str, int | float] = {}
    for key, value in bonus.items():
        if key.endswith("_pct") or not float(value).is_integer():
            result[key] = float(value)
        else:
            result[key] = int(value)
    return result


def apply_percent(value: float, pct: float) -> float:
    """把百分比加成叠到 ``value`` 上（``pct`` 为 0.01 = +1%）。"""
    return float(value) * (1.0 + float(pct))


# ---------------------------------------------------------------------------
# 消耗型商品（"只有本次生效"）
# ---------------------------------------------------------------------------

CONSUME_ON_REINCARNATION = "reincarnation"
"""``consume_on: reincarnation`` —— 买下的效果只对**下一次轮回**生效。

用途见「转世起点」：它抬高新一世的开局等级，用掉之后就没了，
所以不能像永久属性那样一直挂在 ``perk_levels`` 里。
"""


def consumes_on_reincarnation(spec: dict[str, Any]) -> bool:
    """该商品的效果是否在轮回时被消耗掉。"""
    return str(spec.get("consume_on", "")).strip().lower() == CONSUME_ON_REINCARNATION


def pending_reincarnation_bonus(
    levels: dict[str, int],
    cfg: dict[str, Any] | None = None,
) -> dict[str, float]:
    """``levels`` 里**待消耗**部分贡献的加成（如 ``start_level_bonus``）。

    与 :func:`recompute_bonuses` 分开，是因为这部分不该进 ``permanent_bonuses``：
    它是"一次性门票"，写进永久加成会在用掉之后仍然显示。
    """
    specs = catalog(cfg)
    bonus: dict[str, float] = {}
    for item_id, owned in (levels or {}).items():
        spec = specs.get(str(item_id))
        if spec is None or not consumes_on_reincarnation(spec):
            continue
        try:
            count = max(0, int(owned))
        except (TypeError, ValueError):
            continue
        if count <= 0:
            continue
        if level_mode(spec) == LEVEL_CAPPED:
            count = min(count, spec_level_cap(spec))
        for key, value in total_effect(spec, count, cfg).items():
            bonus[key] = bonus.get(key, 0) + value
    return bonus


def consume_reincarnation_entries(
    levels: dict[str, int],
    cfg: dict[str, Any] | None = None,
) -> tuple[dict[str, int], dict[str, int]]:
    """把 ``consume_on: reincarnation`` 的条目清零，返回 ``(新 levels, 被消耗的)``。

    只清 ``perk_levels``，**不退款**——它是本轮已经用掉的东西。
    """
    specs = catalog(cfg)
    consumed: dict[str, int] = {}
    remaining: dict[str, int] = dict(levels or {})
    for item_id, owned in list(remaining.items()):
        spec = specs.get(str(item_id))
        if spec is None or not consumes_on_reincarnation(spec):
            continue
        try:
            count = int(owned)
        except (TypeError, ValueError):
            count = 0
        if count > 0:
            consumed[str(item_id)] = count
            remaining[str(item_id)] = 0
    return remaining, consumed


# ---------------------------------------------------------------------------
# 购买与洗点（写操作）
# ---------------------------------------------------------------------------


def available_points(reinc) -> int:
    """当前可用成就点 = 累计 − 已用。"""
    return max(
        0,
        int(getattr(reinc, "achievement_points", 0) or 0)
        - int(getattr(reinc, "spent_points", 0) or 0),
    )


def owned_level(reinc, item_id: str) -> int:
    """某商品已买等级。"""
    levels = getattr(reinc, "perk_levels", None) or {}
    try:
        return max(0, int(levels.get(str(item_id), 0)))
    except (TypeError, ValueError):
        return 0


def next_price(
    reinc,
    spec: dict[str, Any],
    cfg: dict[str, Any] | None = None,
) -> int | None:
    """下一级价格；已到顶（``capped`` 买满）返回 ``None``。"""
    owned = owned_level(reinc, str(spec.get("id", "")))
    if not has_next_level(spec, owned):
        return None
    return price_for(spec, owned, cfg)


def is_unlocked(reinc, spec: dict[str, Any]) -> bool:
    """是否已满足解锁门槛（``unlock.cycles``，省略即不设门槛）。"""
    unlock = spec.get("unlock") or {}
    if not isinstance(unlock, dict):
        return True
    try:
        need = int(unlock.get("cycles", 1) or 1)
    except (TypeError, ValueError):
        need = 1
    return int(getattr(reinc, "total_cycles", 0) or 0) >= need


def resolve_item(token: str, cfg: dict[str, Any] | None = None) -> str:
    """把玩家输入解析成 item id。

    接受三种写法（忽略大小写与首尾空白）：

    * 编号 ``"1-3"`` / ``"1.3"`` / ``"1_3"``
    * 配置 id ``"legacy_atk"``
    * 中文名 ``"前世武艺"``

    中文名重复时抛 ``ValueError``——配置错误应该在测试里暴露，
    而不是静默取第一个。
    """
    specs = catalog(cfg)
    if not specs:
        raise ValueError("商店里还没有任何商品")

    wanted = (token or "").strip()
    if not wanted:
        raise ValueError("请提供商品编号或名称")

    # 编号：先归一化分隔符，再按 categories 的 order 拼出 "序号-序号"。
    normalized = wanted.replace(".", "-").replace("_", "-").replace("－", "-")
    for number, item_id in number_map(cfg).items():
        if normalized == number:
            return item_id

    lowered = wanted.lower()
    for item_id in specs:
        if lowered == item_id.lower():
            return item_id

    matches = [
        item_id
        for item_id, spec in specs.items()
        if str(spec.get("name") or "").strip().lower() == lowered
    ]
    if len(matches) > 1:
        raise ValueError(f"名称「{wanted}」对应多个商品，请改用编号")
    if matches:
        return matches[0]

    raise ValueError(f"没有这个商品：{wanted}")


def number_map(cfg: dict[str, Any] | None = None) -> dict[str, str]:
    """``{"1-3": "legacy_def"}`` —— 展示编号到商品 id 的映射。

    编号 = ``分类order-该分类内序号``。分类按 ``order`` 排序、条目按配置顺序，
    因此**只增不改**的商品配置里编号是稳定的。
    """
    result: dict[str, str] = {}
    by_cat: dict[str, int] = {}
    for cat in categories(cfg):
        order = int(cat.get("order", 999))
        cat_id = str(cat.get("id"))
        for spec in catalog(cfg).values():
            if str(spec.get("category")) != cat_id:
                continue
            by_cat[cat_id] = by_cat.get(cat_id, 0) + 1
            result[f"{order}-{by_cat[cat_id]}"] = str(spec.get("id"))
    return result


def item_number(item_id: str, cfg: dict[str, Any] | None = None) -> str:
    """商品 id 对应的展示编号；找不到时退回 id 本身。"""
    for number, mapped in number_map(cfg).items():
        if mapped == str(item_id):
            return number
    return str(item_id)


def describe_item(
    reinc,
    spec: dict[str, Any],
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """给展示层用的商品快照（价格 / 等级 / 效果 / 可买性）。"""
    item_id = str(spec.get("id", ""))
    mode = level_mode(spec)
    owned = owned_level(reinc, item_id)
    price = next_price(reinc, spec, cfg)
    available = available_points(reinc)

    one = total_effect(spec, 1, cfg)
    current = total_effect(spec, owned, cfg) if owned else {}
    upcoming = total_effect(spec, owned + 1, cfg) if price is not None else current

    return {
        "id": item_id,
        "number": item_number(item_id, cfg),
        "name": str(spec.get("name") or item_id),
        "desc": str(spec.get("desc") or ""),
        "category": str(spec.get("category") or ""),
        "mode": mode,
        "owned": owned,
        "cap": spec.get("max_level") if mode == LEVEL_CAPPED else None,
        "price": price,
        "available": available,
        "affordable": price is not None and price <= available,
        "gap": max(0, price - available) if price is not None else 0,
        "unlocked": is_unlocked(reinc, spec),
        "per_level": one,
        "current_effect": current,
        "next_effect": upcoming,
        "consumed_on_reincarnation": consumes_on_reincarnation(spec),
    }


def purchase(
    reinc,
    item_id: str,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """买一级。**只改 ``reinc`` 的内存状态，不落库**（调用方负责保存）。

    失败抛 ``ValueError``，且保证 ``reinc`` 不被改动——所有校验都在扣点之前。

    返回 ``{item, name, number, level, price, remaining, before, after, consumed}``，
    其中 ``before`` / ``after`` 是累计效果（用于回执展示"攻击 +1% → +2%"）。
    """
    specs = catalog(cfg)
    spec = specs.get(str(item_id))
    if spec is None:
        raise ValueError("商品不存在")
    if not is_unlocked(reinc, spec):
        unlock = spec.get("unlock") or {}
        raise ValueError(f"需要累计轮回 {unlock.get('cycles')} 次才开放")

    owned = owned_level(reinc, item_id)
    price = next_price(reinc, spec, cfg)
    if price is None:
        raise ValueError("这条已经买满了")

    available = available_points(reinc)
    if price > available:
        raise ValueError(f"成就点不足（需要 {price}，可用 {available}）")

    before = total_effect(spec, owned, cfg)
    levels = dict(getattr(reinc, "perk_levels", None) or {})

    # 消耗品不产生等级（如洗点券），直接发放数量。
    if is_consumable(spec):
        tickets = int((spec.get("effect") or {}).get("respec_ticket", 0) or 0)
        reinc.respec_tickets = int(getattr(reinc, "respec_tickets", 0) or 0) + tickets
        new_level = owned
    else:
        levels[str(item_id)] = owned + 1
        reinc.perk_levels = levels
        new_level = owned + 1

    reinc.spent_points = int(getattr(reinc, "spent_points", 0) or 0) + price
    if not is_consumable(spec):
        reinc.permanent_bonuses = recompute_bonuses(
            {
                key: value
                for key, value in (reinc.perk_levels or {}).items()
                if not consumes_on_reincarnation(specs.get(key, {}))
            },
            cfg,
        )

    return {
        "item": str(item_id),
        "name": str(spec.get("name") or item_id),
        "number": item_number(item_id, cfg),
        "level": new_level,
        "price": price,
        "remaining": available_points(reinc),
        "before": before,
        "after": total_effect(spec, new_level, cfg),
        "consumable": is_consumable(spec),
        "tickets": int(getattr(reinc, "respec_tickets", 0) or 0),
    }


def respec_refund(
    reinc,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """洗点预览：能退多少、清空几条。**不改任何状态**。"""
    specs = catalog(cfg)
    levels = getattr(reinc, "perk_levels", None) or {}
    ratio = 1.0
    respec_cfg = shop_config(cfg).get("respec") or {}
    try:
        ratio = float(respec_cfg.get("refund_ratio", 1.0) or 0)
    except (TypeError, ValueError):
        ratio = 1.0

    refund = 0
    cleared: dict[str, int] = {}
    for item_id, owned in levels.items():
        spec = specs.get(str(item_id))
        if spec is None:
            continue
        try:
            count = int(owned)
        except (TypeError, ValueError):
            continue
        if count <= 0:
            continue
        cleared[str(item_id)] = count
        refund += total_cost(spec, count, cfg)

    return {
        "refund": int(refund * ratio),
        "cleared": cleared,
        "items": len(cleared),
        "levels": sum(cleared.values()),
        "tickets": int(getattr(reinc, "respec_tickets", 0) or 0),
    }


def respec(reinc, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """执行洗点：消耗 1 张券，退还全部已购（**只改内存**）。"""
    preview = respec_refund(reinc, cfg)
    if preview["tickets"] <= 0:
        raise ValueError("没有洗点券了")
    if not preview["cleared"]:
        raise ValueError("现在没有任何可洗的商品")

    reinc.respec_tickets = preview["tickets"] - 1
    reinc.perk_levels = {}
    reinc.permanent_bonuses = {}
    reinc.spent_points = max(
        0, int(getattr(reinc, "spent_points", 0) or 0) - preview["refund"]
    )
    return {
        "refund": preview["refund"],
        "items": preview["items"],
        "levels": preview["levels"],
        "tickets": reinc.respec_tickets,
    }


def grant_respec_tickets(reinc, cfg: dict[str, Any] | None = None) -> int:
    """按 ``shop.respec.free_per_cycle`` 发放洗点券（轮回时调用）。"""
    try:
        count = int(
            (shop_config(cfg).get("respec") or {}).get("free_per_cycle", 0) or 0
        )
    except (TypeError, ValueError):
        return 0
    if count > 0:
        reinc.respec_tickets = int(getattr(reinc, "respec_tickets", 0) or 0) + count
    return count


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def validate_catalog(cfg: dict[str, Any] | None = None) -> list[str]:
    """校验商店配置，返回人类可读的问题列表（空列表 = 配置合法）。

    只校验**结构性**问题，不校验数值平衡——数值是策划的事，
    这里拦住的是"会让运行时炸掉或静默错位"的配置。
    """
    problems: list[str] = []
    cats = categories(cfg)
    orders = [int(cat.get("order", 999)) for cat in cats]
    if len(set(orders)) != len(orders):
        problems.append("categories 的 order 有重复")

    cat_ids = {str(cat.get("id")) for cat in cats}
    for item_id, spec in catalog(cfg).items():
        name = spec.get("name", item_id)
        cat = str(spec.get("category", ""))
        if cat and cat not in cat_ids:
            problems.append(f"{name}：category「{cat}」不在 categories 里")

        effect = spec.get("effect") or {}
        if not isinstance(effect, dict) or not effect:
            problems.append(f"{name}：缺少 effect")
        else:
            unknown = set(effect) - PERK_EFFECT_KEYS
            if unknown:
                problems.append(f"{name}：未知的 effect 键 {sorted(unknown)}")

        mode = level_mode(spec)
        if mode == LEVEL_CAPPED:
            try:
                if int(spec.get("max_level", 0)) <= 0:
                    problems.append(f"{name}：capped 商品的 max_level 必须为正")
            except (TypeError, ValueError):
                problems.append(f"{name}：max_level 必须是整数")
        if mode == _CONSUMABLE and not spec.get("consumable"):
            problems.append(f"{name}：consumable 标记异常")

        cost = spec.get("cost")
        if isinstance(cost, dict):
            if "start" not in cost:
                problems.append(f"{name}：几何涨价缺少 cost.start")
            else:
                try:
                    if float(cost.get("start", 0)) <= 0:
                        problems.append(f"{name}：cost.start 必须为正")
                    if float(cost.get("mult", 1.0)) < 1.0:
                        problems.append(
                            f"{name}：cost.mult 不应小于 1.0（会越买越便宜）"
                        )
                except (TypeError, ValueError):
                    problems.append(f"{name}：cost.start / mult 必须是数字")
        elif isinstance(cost, list | tuple):
            if mode == LEVEL_CAPPED:
                try:
                    cap = int(spec.get("max_level", 0))
                except (TypeError, ValueError):
                    cap = 0
                if cap > 0 and len(cost) != cap:
                    problems.append(
                        f"{name}：cost 有 {len(cost)} 项，与 max_level {cap} 不一致"
                    )
            if not cost:
                problems.append(f"{name}：cost 列表不能为空")
            # 无限等级配列表只能覆盖前几级，之后全靠"取最后一项"，
            # 这通常是配置错误，给出提示但不阻断。
            if mode == LEVEL_INFINITE:
                problems.append(
                    f"{name}：无限等级建议用 cost: {{start, mult}}，"
                    "列表写法会让后面的等级永远取最后一项价格"
                )
        elif not isinstance(cost, int | float):
            problems.append(f"{name}：cost 必须是数字 / 列表 / {{start, mult}} 映射")

    return problems


def projected_growth(
    spec: dict[str, Any],
    owned: int,
    steps: int = 10,
    cfg: dict[str, Any] | None = None,
) -> list[tuple[int, int, float]]:
    """未来 ``steps`` 级的价格与累计效果，用于给玩家看"越买越贵、越买越强"。

    返回 ``[(等级, 该级价格, 累计百分比效果), ...]``；平值类的累计效果
    单独用 :func:`total_effect` 取。
    """
    out: list[tuple[int, int, float]] = []
    for i in range(max(0, int(steps))):
        lvl = owned + i + 1
        price = price_for(spec, lvl - 1, cfg)
        effects = total_effect(spec, lvl, cfg)
        pct = sum(v for k, v in effects.items() if k.endswith("_pct"))
        out.append((lvl, price, pct))
    return out


def geometric_preview(start: float, mult: float, count: int) -> list[int]:
    """几何涨价的直观预览（调试/预览脚本用）。"""
    return [max(0, round(start * math.pow(mult, i))) for i in range(max(0, count))]
