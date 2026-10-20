"""局内运行状态：RogueRun 数据类与楼层状态机。

把原本散落在 floor.py 里的裸 dict ``run`` 提升为有类型约束的数据类，
把 pending type 的分发逻辑从 if/elif 链提升为状态模式。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ._common import floor_rng, passive_effects

# ---------------------------------------------------------------------------
# RogueRun — 替代裸 dict
# ---------------------------------------------------------------------------


@dataclass
class RogueRun:
    """一局肉鸽的完整运行状态。

    替代原来的 ``dict[str, Any]``，提供属性访问与类型约束。
    序列化/反序列化通过 :meth:`to_dict` / :meth:`from_dict` 完成，
    与 database 层的 ``rogue.run_state`` 保持兼容。
    """

    seed: int = 0
    floor: int = 1
    hp: int = 0
    hp_max: int = 0
    coins: int = 0
    exp: int = 0
    buffs: dict[str, int] = field(
        default_factory=lambda: {"atk": 0, "def": 0, "spd": 0}
    )
    boss_wins: int = 0
    cleared: bool = False
    unlocks: list[str] = field(default_factory=list)
    relics: list[str] = field(default_factory=list)
    seen_events: list[str] = field(default_factory=list)
    pending: dict[str, Any] = field(default_factory=dict)
    started_at: str = ""

    # -- 序列化 ---------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict，可直接赋给 ``rogue.run_state``。"""
        return {
            "seed": self.seed,
            "floor": self.floor,
            "hp": self.hp,
            "hp_max": self.hp_max,
            "coins": self.coins,
            "exp": self.exp,
            "buffs": dict(self.buffs),
            "boss_wins": self.boss_wins,
            "cleared": self.cleared,
            "unlocks": list(self.unlocks),
            "relics": list(self.relics),
            "seen_events": list(self.seen_events),
            "pending": dict(self.pending),
            "started_at": self.started_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RogueRun | None:
        """从 dict 反序列化；空 dict 或 None 返回 None（表示无局）。"""
        if not data:
            return None
        return cls(
            seed=data.get("seed", 0),
            floor=data.get("floor", 1),
            hp=data.get("hp", 0),
            hp_max=data.get("hp_max", 0),
            coins=data.get("coins", 0),
            exp=data.get("exp", 0),
            buffs=data.get("buffs", {"atk": 0, "def": 0, "spd": 0}),
            boss_wins=data.get("boss_wins", 0),
            cleared=data.get("cleared", False),
            unlocks=list(data.get("unlocks", [])),
            relics=list(data.get("relics", [])),
            seen_events=list(data.get("seen_events", [])),
            pending=data.get("pending", {}),
            started_at=data.get("started_at", ""),
        )

    # -- 工厂 -----------------------------------------------------------------

    @classmethod
    def create(cls, pet, cfg: dict[str, Any], rogue=None) -> RogueRun:
        """开一局：局内生命取养成 HP 上限（+被动 max_hp +遗物 max_hp_mult），从第 1 层开始。"""
        from .relics import relic_flat, relic_multipliers

        hp_max = int(pet.attributes.get("hp", 100)) + int(
            passive_effects(pet).get("max_hp", 0)
        )
        coins = 0
        if rogue is not None:
            mults = relic_multipliers(rogue)
            hp_max = max(1, round(hp_max * mults.get("max_hp_mult", 1.0)))
            coins = int(relic_flat(rogue, "start_coins"))
        return cls(
            seed=random.randint(1, 10**9),
            floor=1,
            hp=hp_max,
            hp_max=hp_max,
            coins=coins,
            started_at=datetime.now().isoformat(timespec="seconds"),
        )


# ---------------------------------------------------------------------------
# FloorState — 楼层状态机
# ---------------------------------------------------------------------------


class FloorState:
    """楼层状态的基类：每种 pending type 对应一个子类。

    每个子类实现两个方法：
    - :meth:`render` — 把当前状态渲染成玩家可见的消息文本
    - :meth:`handle` — 处理玩家的编号输入，返回 (响应文本, 是否已终结)

    状态转换通过返回新的 FloorState 实例完成；返回 None 表示状态不变。
    """

    type_name: str = ""

    def render(self, run: RogueRun, cfg: dict[str, Any], chosen: set[str]) -> str:
        raise NotImplementedError

    def handle(
        self, run: RogueRun, cfg: dict[str, Any], number: int, pet, rogue
    ) -> tuple[str, FloorState | None]:
        """处理编号输入。返回 (响应文本, 新状态或 None)。"""
        raise NotImplementedError


class NeedAPState(FloorState):
    """行动点不足，等待明天重置。"""

    type_name = "need_ap"

    def render(self, run: RogueRun, cfg: dict[str, Any], chosen: set[str]) -> str:
        from ._common import _text

        return _text(
            "need_ap",
            "行动点不足，原地待命；明天 00:00 重置后发送「/宠物.肉鸽 继续」。",
        )

    def handle(
        self, run: RogueRun, cfg: dict[str, Any], number: int, pet, rogue
    ) -> tuple[str, FloorState | None]:
        # 由外部 _advance 处理
        return "", None


class BattleState(FloorState):
    """战斗状态（首领层 / 普通层选中的战斗选项）。"""

    type_name = "battle"

    def __init__(
        self,
        boss: bool = False,
        boss_index: int = 0,
        elite: bool = False,
        enemy_index: int | None = None,
    ):
        self.boss = boss
        self.boss_index = boss_index
        self.elite = elite
        self.enemy_index = enemy_index

    def to_pending(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": "battle",
            "boss": self.boss,
            "boss_index": self.boss_index,
        }
        if self.elite:
            d["elite"] = True
        if self.enemy_index is not None:
            d["enemy_index"] = int(self.enemy_index)
        return d

    @classmethod
    def from_pending(cls, pending: dict[str, Any]) -> BattleState:
        enemy_index = pending.get("enemy_index")
        return cls(
            boss=pending.get("boss", False),
            boss_index=pending.get("boss_index", 0),
            elite=pending.get("elite", False),
            enemy_index=None if enemy_index is None else int(enemy_index),
        )

    def render(self, run: RogueRun, cfg: dict[str, Any], chosen: set[str]) -> str:
        from ._common import _text
        from .tower import _gen_monster, _monster_label, region_of

        floor = run.floor
        region = region_of(floor, cfg)
        monster = _gen_monster(
            floor,
            region,
            cfg,
            floor_rng(run.seed, floor, "battle"),
            boss_index=self.boss_index if self.boss else None,
            elite=self.elite,
            pool_index=None if self.elite else self.enemy_index,
            elite_index=self.enemy_index if self.elite else None,
        )
        if self.boss:
            return _text(
                "boss_floor",
                "🎲 本层首领随机选定：{monster}！回复「1」开战。",
                floor=floor,
                monster=_monster_label(monster),
                region=region.get("name", ""),
            )
        return _text(
            "battle_prompt", "回复「1」迎战。", monster=_monster_label(monster)
        )

    def handle(
        self, run: RogueRun, cfg: dict[str, Any], number: int, pet, rogue
    ) -> tuple[str, FloorState | None]:
        from ._common import _text

        if number != 1:
            return _text("invalid", "回复「1」迎战。"), None
        # 由外部 _do_battle 处理
        return "", None


class FloorChoiceState(FloorState):
    """普通层三选一（若干战斗位 + 至多一个事件 + 可能的商店）。

    ``options`` 按**展示顺序**排列（商店优先占位，其余顺延）；``enemies`` 与之等长，
    战斗位存预抽好的敌人池下标（普通层取 ``monsters``、精英层取 ``elites``，
    层内各战斗位互不相同），非战斗位为 ``None``。旧存档的 pending 没有 ``enemies``，
    战斗位恒为 ``None``，沿用 rng 随机生成兜底。
    """

    type_name = "floor"

    def __init__(
        self,
        options: list[str] | None = None,
        elite: bool = False,
        event_id: str = "",
        stock: list[str] | None = None,
        enemies: list[int | None] | None = None,
    ):
        self.options = options or []
        self.elite = elite
        self.event_id = event_id
        self.stock = stock or []
        self.enemies = list(enemies or [])

    def to_pending(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": "floor", "options": list(self.options)}
        if self.elite:
            d["elite"] = True
        if self.event_id:
            d["event_id"] = self.event_id
        if self.stock:
            d["stock"] = list(self.stock)
        if any(index is not None for index in self.enemies):
            d["enemies"] = [
                None if index is None else int(index) for index in self.enemies
            ]
        return d

    @classmethod
    def from_pending(cls, pending: dict[str, Any]) -> FloorChoiceState:
        return cls(
            options=pending.get("options", []),
            elite=pending.get("elite", False),
            event_id=pending.get("event_id", ""),
            stock=pending.get("stock", []),
            enemies=pending.get("enemies") or [],
        )

    def enemy_at(self, number: int) -> int | None:
        """第 ``number``（从 1 起）个选项预定的敌人池下标；非战斗位/旧存档为 None。"""
        index = number - 1
        if 0 <= index < len(self.enemies):
            return self.enemies[index]
        return None

    def _option_monster(
        self, cfg: dict[str, Any], run: RogueRun, enemy_index: int | None
    ) -> dict[str, Any]:
        """该战斗位展示的对手：按预抽下标生成；旧存档（无 ``enemies``）沿用 rng。"""
        from .tower import _gen_monster, region_of

        floor = run.floor
        region = region_of(floor, cfg)
        return _gen_monster(
            floor,
            region,
            cfg,
            floor_rng(run.seed, floor, "battle"),
            elite=self.elite,
            pool_index=None if self.elite else enemy_index,
            elite_index=enemy_index if self.elite else None,
        )

    def render(self, run: RogueRun, cfg: dict[str, Any], chosen: set[str]) -> str:
        from ._common import _text
        from .tower import _monster_label, region_of

        labels = {
            "battle": _text("opt_battle", "战斗"),
            "event": _text("opt_event", "事件"),
            "shop": _text("opt_shop", "商店"),
        }
        stock_text = _stock_preview_text(self.stock, cfg)
        lines = [
            _text(
                "floor_header",
                "—— 第 {floor} 层 · {region} ——",
                floor=run.floor,
                region=region_of(run.floor, cfg).get("name", ""),
            )
        ]
        for idx, opt in enumerate(self.options, start=1):
            line = f"{idx}. {labels.get(opt, opt)}"
            if opt == "battle":
                monster = self._option_monster(cfg, run, self.enemy_at(idx))
                if self.elite:
                    line += _text(
                        "elite_hint",
                        "（⚠️ 精英出没：{monster}）",
                        monster=_monster_label(monster),
                    )
                else:
                    line += _text(
                        "battle_hint",
                        "（强敌：{monster}）",
                        monster=_monster_label(monster),
                    )
            elif opt == "shop":
                line += _text("shop_hint", "（{stock}）", stock=stock_text)
            else:
                line += _text("event_hint", "（未知遭遇）")
            lines.append(line)
        lines.append(_text("floor_reply", "回复编号选择路线。"))
        return "\n".join(lines)

    def handle(
        self, run: RogueRun, cfg: dict[str, Any], number: int, pet, rogue
    ) -> tuple[str, FloorState | None]:
        from ._common import _text

        if not 1 <= number <= len(self.options):
            return _text(
                "invalid_choice", "编号超出范围。", options=str(len(self.options))
            ), None
        return "", None  # 由外部分发


class EventState(FloorState):
    """事件状态（含补给事件）。"""

    type_name = "event"

    def __init__(self, event_id: str = "", supply: bool = False):
        self.event_id = event_id
        self.supply = supply

    def to_pending(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": "event", "event_id": self.event_id}
        if self.supply:
            d["supply"] = True
        return d

    @classmethod
    def from_pending(cls, pending: dict[str, Any]) -> EventState:
        return cls(
            event_id=pending.get("event_id", ""),
            supply=pending.get("supply", False),
        )

    def render(self, run: RogueRun, cfg: dict[str, Any], chosen: set[str]) -> str:
        from ._common import _find_event, _text
        from .floor import _choice_effect_note
        from .tower import region_of

        floor = run.floor
        region = region_of(floor, cfg)
        event = _find_event(self.event_id, region, cfg)
        if event is None:
            return _text("event_missing", "事件丢失，继续前进。")
        lines = [
            _text("event_prompt", "🎲 遭遇事件，回复编号选择行动：", text=event["text"])
        ]
        event_id = str(event.get("id", ""))
        heal_mult = 1.0
        if self.supply:
            heal_mult = float((region.get("shop") or {}).get("heal_mult", 1))
        for idx, choice in enumerate(event.get("choices", []), start=1):
            line = f"{idx}. {choice['text']}"
            if f"{event_id}:{idx}" in chosen:
                effect = dict(choice.get("effect", {}) or {})
                if heal_mult != 1.0 and effect.get("run_hp"):
                    effect["run_hp"] = round(effect["run_hp"] * heal_mult)
                line += f" [{_choice_effect_note(effect)}]"
            lines.append(line)
        return "\n".join(lines)

    def handle(
        self, run: RogueRun, cfg: dict[str, Any], number: int, pet, rogue
    ) -> tuple[str, FloorState | None]:
        # 由外部 _resolve_event 处理
        return "", None


class ShopState(FloorState):
    """商店状态。"""

    type_name = "shop"

    def __init__(self, stock: list[str] | None = None):
        self.stock = stock or []

    def to_pending(self) -> dict[str, Any]:
        return {"type": "shop", "stock": list(self.stock)}

    @classmethod
    def from_pending(cls, pending: dict[str, Any]) -> ShopState:
        return cls(stock=pending.get("stock", []))

    def render(self, run: RogueRun, cfg: dict[str, Any], chosen: set[str]) -> str:
        return _shop_text_from_run(run, self.stock, cfg)

    def handle(
        self, run: RogueRun, cfg: dict[str, Any], number: int, pet, rogue
    ) -> tuple[str, FloorState | None]:
        # 由外部 _buy / _advance 处理
        return "", None


class RelicChoiceState(FloorState):
    """遗物三选一状态。"""

    type_name = "relic_choice"

    def __init__(self, offer: list[str] | None = None):
        self.offer = offer or []

    def to_pending(self) -> dict[str, Any]:
        return {"type": "relic_choice", "offer": list(self.offer)}

    @classmethod
    def from_pending(cls, pending: dict[str, Any]) -> RelicChoiceState:
        return cls(offer=pending.get("offer", []))

    def render(self, run: RogueRun, cfg: dict[str, Any], chosen: set[str]) -> str:
        from ._common import _text
        from .relics import relic_effect_note, relic_specs

        specs = relic_specs()
        lines = [_text("relic_choice", "🎁 遗物奖励层（回复编号三选一）：")]
        if not self.offer:
            lines.append(_text("relic_none", "遗物已集齐，直接前进。"))
        for idx, relic_id in enumerate(self.offer, start=1):
            spec = specs.get(relic_id, {})
            lines.append(
                _text(
                    "relic_option",
                    "{idx}. {name}（{stat}） — {desc}",
                    idx=idx,
                    name=spec.get("name", relic_id),
                    stat=relic_effect_note(spec.get("effect") or {}),
                    desc=spec.get("desc", ""),
                )
            )
        return "\n".join(lines)

    def handle(
        self, run: RogueRun, cfg: dict[str, Any], number: int, pet, rogue
    ) -> tuple[str, FloorState | None]:
        # 由外部处理
        return "", None


# ---------------------------------------------------------------------------
# 状态工厂
# ---------------------------------------------------------------------------

_STATE_CLASSES: dict[str, type[FloorState]] = {
    "need_ap": NeedAPState,
    "battle": BattleState,
    "floor": FloorChoiceState,
    "event": EventState,
    "shop": ShopState,
    "relic_choice": RelicChoiceState,
}


def state_from_pending(pending: dict[str, Any]) -> FloorState:
    """从 pending dict 构造对应的 FloorState 实例。"""
    ptype = pending.get("type", "")
    cls = _STATE_CLASSES.get(ptype)
    if cls is None:
        return FloorState()
    if cls is NeedAPState:
        return NeedAPState()
    return cls.from_pending(pending)


# ---------------------------------------------------------------------------
# 商店渲染辅助（从 floor.py 移过来，避免循环导入）
# ---------------------------------------------------------------------------


def _stock_preview_text(stock: list[str], cfg: dict[str, Any]) -> str:
    from ._common import _text
    from .relics import relic_effect_note, relic_specs

    names = []
    for relic_id in stock:
        spec = relic_specs().get(relic_id)
        if spec:
            note = relic_effect_note(spec.get("effect") or {})
            label = f"{spec['name']}（{note}，" if note else f"{spec['name']}（"
            names.append(f"{label}{_relic_price(spec, cfg)}金）")
    return "、".join(names) or _text("shop_empty", "空空如也")


def _relic_price(spec: dict[str, Any], cfg: dict[str, Any]) -> int:
    tier_price = cfg.get("shop", {}).get("tier_price") or {}
    return int(tier_price.get(spec.get("rarity", "normal"), 25))


def _shop_text_from_run(run: RogueRun, stock: list[str], cfg: dict[str, Any]) -> str:
    from ...utils.texts import text
    from ._common import _text
    from .relics import relic_effect_note, relic_specs

    lines = [_text("shop_header", "🛒 遗物商店（金币 {coins}）", coins=run.coins)]
    for idx, relic_id in enumerate(stock, 1):
        spec = relic_specs().get(relic_id)
        if not spec:
            continue
        lines.append(
            _text(
                "relic_shop_line",
                "{idx}. {name}（{label}｜{stat}）— {price}金",
                idx=idx,
                name=spec.get("name", relic_id),
                label=text(
                    f"rogue.tiers.{spec.get('rarity', 'normal')}",
                    spec.get("rarity", "normal"),
                ),
                stat=relic_effect_note(spec.get("effect") or {}),
                price=_relic_price(spec, cfg),
            )
        )
    lines.append(_text("shop_reply", "回复编号购买，回复 0 离开。"))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# run 生命周期管理
# ---------------------------------------------------------------------------


def _has_run(rogue) -> bool:
    """是否有进行中的局。"""
    return bool(rogue.run_state)


def _chosen_set(rogue) -> set[str]:
    """该玩家历史选过的事件选项集合（"事件id:编号"）。"""
    return set(getattr(rogue, "choices", None) or [])


async def start(pet, rogue) -> str:
    """入场：**有肉鸽入场券则优先用券且不扣行动点**，无券时按原规则扣入场行动点。

    入场券是 LK 背包物品（签到发放，见 ``items.py``）；用券成功时当次入场
    完全不消耗行动点。没有任何券、行动点也不够时给出明确回执。
    """
    from ..interaction import check_action_point, consume_action_point
    from ._common import (
        ENTRY_COST_KEY,
        _entry_cost,
        _load_rogue_config,
        _persist,
        _text,
    )

    if _has_run(rogue):
        return await resume(pet, rogue)
    cfg = _load_rogue_config()

    ticket_used, ticket_note = _use_entry_ticket(pet)
    if not ticket_used:
        try:
            check_action_point(pet, ENTRY_COST_KEY)
        except ValueError as exc:
            return _text(
                "entry_no_ap",
                "今天没有入场券，行动点也不够（需要 {cost} 点）：{msg}",
                cost=_entry_cost(),
                msg=str(exc),
            )
        consume_action_point(pet, ENTRY_COST_KEY)

    try:
        run = RogueRun.create(pet, cfg, rogue)
        _present_floor(run, cfg, rogue)
        rogue.run_state = run.to_dict()
        await _persist(rogue)
    except Exception:
        # 建局/落库失败时把刚扣掉的券补回去，避免「券没了、局没开」。
        if ticket_used:
            _refund_entry_ticket(pet)
        raise
    header = _text(
        "started",
        "🗼 入场成功！",
        floor=run.floor,
        action_points=pet.action_points,
        coins=run.coins,
    )
    return (header + ("\n" + ticket_note if ticket_note else "")) + (
        "\n" + _render_pending(run, cfg, _chosen_set(rogue))
    )


def _change_entry_ticket(pet, delta: int) -> bool:
    """增减 LK 背包里的入场券；成功返回 ``True``。

    券是 LK 背包物品，读写都走 LK 账户。``get_user_data`` 返回的是**未加锁的副本**，
    必须放进 ``with`` 块，退出时才会由 ``__exit__`` 落库——所以变动一定要在 with 内完成，
    否则券只是少在内存里，数据库仍保留。
    """
    from ...items import rogue_ticket_name

    name = rogue_ticket_name()
    player_id = str(getattr(pet, "player_id", "") or "")
    if not player_id:
        return False
    try:
        from ATRI.system.lkapi.entity.user import user_manager

        with user_manager.get_user_data(player_id) as user_data:
            if delta < 0 and not user_data.backpack.bp_has_item(name):
                return False
            return bool(user_data.item_num_change(name, delta))
    except Exception:  # noqa: BLE001 - LK 账户不可用时按「没有券」处理
        return False


def _use_entry_ticket(pet) -> tuple[bool, str]:
    """尝试消耗 1 张肉鸽入场券；成功返回 ``(True, 回执文案)``。

    LK 侧异常（未登录/物品未注册/背包不可用）一律视为没有券，回落到行动点入场。
    """
    from ...items import rogue_ticket_name
    from ._common import _text

    if not _change_entry_ticket(pet, -1):
        return False, ""
    name = rogue_ticket_name()
    return True, _text(
        "entry_ticket_used",
        "🎫 消耗 1 张{name}入场（本局不消耗行动点）",
        name=name,
    )


def _refund_entry_ticket(pet) -> None:
    """把入场券退回 LK 背包（建局失败时使用）。"""
    _change_entry_ticket(pet, 1)


async def resume(pet, rogue) -> str:
    """继续/重发当前局的待应答提示。"""
    from ._common import _load_rogue_config, _persist, _text

    if not _has_run(rogue):
        return _text("no_run", "当前没有进行中的局。发送「/宠物.肉鸽 开始」入场。")
    run = RogueRun.from_dict(rogue.run_state)
    cfg = _load_rogue_config()
    state = state_from_pending(run.pending)
    if state.type_name == "need_ap":
        return await advance(pet, rogue, first=False)
    await _persist(rogue)
    return (
        _progress_line(run, cfg) + "\n" + _render_pending(run, cfg, _chosen_set(rogue))
    )


def progress(rogue) -> str:
    """历史、本局遗物与图鉴概览。"""
    from ._common import _load_rogue_config, _text
    from .relics import _relic_display, _run_relics, relic_specs

    lines = [
        _text(
            "stats",
            "历史统计",
            best=rogue.best_floor,
            runs=rogue.total_runs,
            clears=rogue.clears,
        )
    ]
    lines.append(
        _text(
            "codex",
            "📇 遗物图鉴：{owned}/{total}",
            owned=len(rogue.relics),
            total=len(relic_specs()),
        )
    )
    if _has_run(rogue):
        cfg = _load_rogue_config()
        run = RogueRun.from_dict(rogue.run_state)
        lines.insert(0, _progress_line(run, cfg))
        run_relics = _run_relics(rogue)
        if run_relics:
            names = "、".join(_relic_display(relic_id) for relic_id in run_relics)
            lines.insert(
                1,
                _text(
                    "run_relics",
                    "🗡 本局遗物（{count} 件，仅本局生效）：{names}",
                    count=len(run_relics),
                    names=names,
                ),
            )
    else:
        lines.insert(
            0, _text("no_run", "当前没有进行中的局。发送「/宠物.肉鸽 开始」入场。")
        )
    return "\n".join(lines)


async def retreat(pet, rogue) -> str:
    """撤退：立即按到达层数结算并结束本局。"""
    from ._common import _text
    from .settle import settle_run

    if not _has_run(rogue):
        return _text("no_run", "当前没有进行中的局。发送「/宠物.肉鸽 开始」入场。")
    return await settle_run(pet, rogue, reason="retreat")


async def advance(pet, rogue, *, first: bool = False) -> str:
    """解决完一层后前进：扣步进点、层数 +1、展示下一层。"""
    from ..interaction import check_action_point, consume_action_point
    from ._common import (
        STEP_COST_KEY,
        _load_rogue_config,
        _persist,
        _text,
    )
    from .tower import region_of

    cfg = _load_rogue_config()
    run = RogueRun.from_dict(rogue.run_state)
    if first:
        pass
    else:
        try:
            check_action_point(pet, STEP_COST_KEY)
        except ValueError:
            run.pending = {"type": "need_ap"}
            rogue.run_state = run.to_dict()
            await _persist(rogue)
            return _text(
                "step_no_ap",
                "行动点不足，原地待命；明天 00:00 重置后发送「/宠物.肉鸽 继续」。",
            )
        consume_action_point(pet, STEP_COST_KEY)

    old_region = region_of(run.floor, cfg)
    run.floor += 1
    new_region = region_of(run.floor, cfg)
    _present_floor(run, cfg, rogue)
    rogue.run_state = run.to_dict()
    await _persist(rogue)
    rendered = _render_pending(run, cfg, _chosen_set(rogue))
    if new_region["id"] != old_region["id"]:
        entered = _text(
            "region_enter",
            "🗺 抵达新区域【{name}】{desc}",
            name=new_region.get("name", ""),
            desc=new_region.get("desc", ""),
        )
        return entered + "\n" + rendered
    return rendered


# ---------------------------------------------------------------------------
# 楼层生成与渲染（内部辅助）
# ---------------------------------------------------------------------------


def _present_floor(run: RogueRun, cfg: dict[str, Any], rogue=None) -> None:
    """生成当前层的待应答状态。"""
    from ._common import floor_rng
    from .relics import relic_offer
    from .tower import (
        _is_elite_floor,
        boss_floor_set,
        region_of,
    )

    floor = run.floor
    region = region_of(floor, cfg)

    if floor in boss_floor_set(cfg):
        candidates = (cfg.get("bosses") or {}).get(region.get("id", ""), [])
        boss_index = 0
        if candidates:
            boss_index = floor_rng(run.seed, floor, "boss").randrange(len(candidates))
        state = BattleState(boss=True, boss_index=boss_index)
        run.pending = state.to_pending()
        return

    supply_cfg = cfg.get("supply", {}) or {}
    s_offset = int(supply_cfg.get("floor_offset", 1) or 0)
    if s_offset and floor == int(region["end"]) - s_offset and supply_cfg.get("event"):
        state = EventState(event_id=supply_cfg["event"]["id"], supply=True)
        run.pending = state.to_pending()
        return

    relic_floor = int(region.get("relic_floor") or 0)
    if relic_floor and floor == relic_floor:
        offer = relic_offer(rogue, floor, run.seed) if rogue is not None else []
        state = RelicChoiceState(offer=offer)
        run.pending = state.to_pending()
        return

    options, event_id, stock, enemies = _normal_floor_options(
        run, region, cfg, floor, rogue
    )
    state = FloorChoiceState(
        options=options,
        elite=_is_elite_floor(run.to_dict(), region, cfg),
        event_id=event_id,
        stock=stock,
        enemies=enemies,
    )
    run.pending = state.to_pending()


def _normal_floor_options(
    run: RogueRun, region: dict[str, Any], cfg: dict[str, Any], floor: int, rogue
) -> tuple[list[str], str, list[str], list[int | None]]:
    """普通层的三选一内容：商店规则优先，其余选项依次判事件，战斗位填不同敌人。

    规则（``roguelike.yaml`` 的 ``floor_choice`` 块）：

    1. **商店规则优先**：允许出现的层（``shop.no_before_gap`` 之后）按 ``shop.chance``
       逐层判定（区域级 seed，可复现），不再每层必出；一旦刷出，商店占第 1 个名额，
       先于事件判定定下来，不参与概率抽取；
    2. **事件判定**：剩下的名额按顺序用 ``event_chance``（0.1 / 0.2 / 0.3）逐个判定，
       **顺序判定、只会出现一个事件**——一旦命中就停止，后面的选项直接成为战斗。
       因此第 1 位命中即 10%，第 2 位 90%×20%，第 3 位 90%×80%×30%；
    3. **战斗位**：一律从对应敌人池抽**互不相同**的敌人填满——普通层用该区域
       ``region.monsters``、精英层用 ``region.elites``，保证同一层的几条战斗路线
       不是同一只怪（精英遭遇的概率/保底规则不变，只改层内各战斗位的抽取方式；
       旧存档没有预抽下标，沿用 rng 随机生成兜底）；
    4. **排列**：生成顺序即展示顺序，不再打乱（商店在第 1 位，其余顺延）。

    返回 ``(options, event_id, stock, enemies)``，``enemies`` 与 ``options`` 等长。
    """
    from .tower import _is_elite_floor, _pick_enemy_indices, _region_events

    choice_cfg = cfg.get("floor_choice", {}) or {}
    slot_count = max(1, int(choice_cfg.get("option_count", 3)))
    chances = [float(x) for x in (choice_cfg.get("event_chance") or [0.1, 0.2, 0.3])]
    is_elite = _is_elite_floor(run.to_dict(), region, cfg)

    options: list[str] = []
    stock: list[str] = []
    shop_cfg = cfg.get("shop") or {}
    gap = int(shop_cfg.get("no_before_gap", 5))
    shop_rng = floor_rng(run.seed, floor, "shop")
    if floor >= int(region["start"]) + gap and shop_rng.random() < float(
        shop_cfg.get("chance", 0) or 0
    ):
        options.append("shop")
        stock = _shop_stock(run, cfg, shop_rng)

    event_id = ""
    remaining = slot_count - len(options)
    event_rng = floor_rng(run.seed, floor, "event")
    events = _region_events(region)
    seen = set(run.seen_events)
    pool = [event for event in events if event.get("id") not in seen] or events
    for position in range(remaining):
        chance = chances[position] if position < len(chances) else 0.0
        if not event_id and pool and event_rng.random() < chance:
            picked = pool[event_rng.randrange(len(pool))]
            event_id = picked["id"]
            run.seen_events.append(picked["id"])
            options.append("event")
        else:
            options.append("battle")

    battle_slots = options.count("battle")
    # 战斗位一律预抽互不相同的敌人下标：普通层取 monsters、精英层取 elites
    # （精英遭遇的概率/保底规则不变，只改层内各战斗位的抽取方式；旧存档
    # pending 没有 enemies，回落到 rng 随机生成）。
    enemies: list[int | None] = [None] * len(options)
    if battle_slots:
        indices = _pick_enemy_indices(
            region, run.seed, floor, battle_slots, elite=is_elite
        )
        cursor = 0
        for index, opt in enumerate(options):
            if opt == "battle" and cursor < len(indices):
                enemies[index] = indices[cursor]
                cursor += 1
    return options, event_id, stock, enemies


def _shop_stock(run: RogueRun, cfg: dict[str, Any], rng: random.Random) -> list[str]:
    """按 tier（normal/rare/shop）各抽 1 件遗物，排除本局已持有。"""
    from .relics import relic_specs

    shop_cfg = cfg.get("shop", {}) or {}
    tiers = shop_cfg.get("stock_tiers") or ["normal", "rare", "shop"]
    specs = relic_specs()
    owned = set(run.relics)
    stock: list[str] = []
    for tier in tiers:
        pool = [
            relic_id
            for relic_id, spec in specs.items()
            if spec.get("rarity") == tier
            and relic_id not in owned
            and relic_id not in stock
        ]
        if pool:
            stock.append(pool[rng.randrange(len(pool))])
    return stock


def _render_pending(run: RogueRun, cfg: dict[str, Any], chosen: set[str]) -> str:
    """把当前 pending 渲染成一条可回复的消息。"""
    from ._common import _text

    state = state_from_pending(run.pending)
    if state.type_name == "":
        return _text("unknown", "状态异常，发送「/宠物.肉鸽 进度」查看。")
    return state.render(run, cfg, chosen)


def _progress_line(run: RogueRun, cfg: dict[str, Any]) -> str:
    from ._common import _text
    from .tower import region_of

    region = region_of(run.floor, cfg)
    return _text(
        "active",
        "进行中的局",
        floor=run.floor,
        region=region.get("name", ""),
        hp=run.hp,
        hp_max=run.hp_max,
        coins=run.coins,
        exp=run.exp,
    )
