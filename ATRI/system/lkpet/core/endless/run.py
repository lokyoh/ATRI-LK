"""无尽模式的一局：状态、推进、结算。

一局的结构：

    RogueRun  floor / region / hp / mp / coins / relics / progression / pending
    pending   玩家现在该做什么（floor 选项 / 战斗 / 商店 / 升级选属性）

**不碰养成数据**：本模块只在内存里推进，落盘由外层调 database。
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from .config import load_endless_config, template_attributes
from .floor import (
    build_options,
    region_of,
    stage_of,
)
from .progression import (
    Progression,
    combat_attributes,
    new_progression,
    pending_choices,
)


def new_run_id() -> str:
    """一局的标识：本地时间戳 + 4 位随机后缀。

    时间戳让文件名本身可排序，后缀防同秒重开撞名。
    """
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    return f"{stamp}-{secrets.token_hex(2)}"


@dataclass
class RogueRun:
    """一局的完整状态。"""

    run_id: str = ""
    seed: int = 0
    floor: int = 1
    region: str = ""
    hp: int = 0
    hp_max: int = 0
    mp: int = 0
    mp_max: int = 0
    coins: int = 0
    relics: list[str] = field(default_factory=list)
    equip: dict[str, list[str]] = field(default_factory=dict)
    skills: dict[str, list[str]] = field(default_factory=dict)
    seen_events: list[str] = field(default_factory=list)
    progression: Progression = field(default_factory=new_progression)
    pending: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "seed": self.seed,
            "floor": self.floor,
            "region": self.region,
            "hp": self.hp,
            "hp_max": self.hp_max,
            "mp": self.mp,
            "mp_max": self.mp_max,
            "coins": self.coins,
            "relics": list(self.relics),
            "equip": dict(self.equip),
            "skills": {k: list(v) for k, v in self.skills.items()},
            "seen_events": list(self.seen_events),
            "progression": self.progression.to_dict(),
            "pending": dict(self.pending),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RogueRun:
        if not data:
            return cls()
        return cls(
            run_id=str(data.get("run_id", "")) or new_run_id(),
            seed=int(data.get("seed", 0) or 0),
            floor=max(1, int(data.get("floor", 1) or 1)),
            region=str(data.get("region", "")),
            hp=int(data.get("hp", 0) or 0),
            hp_max=int(data.get("hp_max", 0) or 0),
            mp=int(data.get("mp", 0) or 0),
            mp_max=int(data.get("mp_max", 0) or 0),
            coins=int(data.get("coins", 0) or 0),
            relics=[str(x) for x in (data.get("relics") or [])],
            equip={
                str(k): [str(i) for i in (v or [])]
                for k, v in (data.get("equip") or {}).items()
            },
            skills={
                str(k): [str(i) for i in (v or [])]
                for k, v in (data.get("skills") or {}).items()
            },
            seen_events=[str(x) for x in (data.get("seen_events") or [])],
            progression=Progression.from_dict(data.get("progression")),
            pending=dict(data.get("pending") or {}),
        )


def new_run(seed: int, cfg: dict[str, Any] | None = None) -> RogueRun:
    """开一局：模板面板做上限、满血满蓝、金币 0。"""
    cfg = cfg or load_endless_config()
    base = template_attributes(cfg)
    run = RogueRun(run_id=new_run_id(), seed=int(seed), floor=1)
    run.region = region_of(1, run.seed, cfg)
    run.progression = new_progression(cfg)
    attrs = combat_attributes(base, run.progression)
    run.hp_max = int(attrs.get("hp", 1))
    run.hp = run.hp_max
    run.mp_max = int(attrs.get("mp", 0))
    run.mp = run.mp_max
    # 开局抽卡（设计第三章）：先遗物 3 选 1，再技能 4 选 1 × 3，最后进第 1 层。
    # 这三步是**待办队列**，一层层弹给玩家；全部选完后才给第 1 层的选项。
    import random as _random

    from .relics import offer_relics
    from .skills import offer_skills

    rng = _random.Random(f"{run.seed}:opening")
    picks: list[dict[str, Any]] = []
    # 遗物 3 选 1（设计第三章）——数量是 3，不是 1
    relics = offer_relics(rng, 1, None, 3)
    if relics:
        picks.append({"type": "open_relic", "options": relics})
    # 三次 4 选 1 必须是**三批不同的技能**：同一份列表复用三次，
    # 第二三次就成了「已学会的那几个再选一遍」，等于没刷新。
    offered: list[str] = []
    for _ in range(3):
        batch = offer_skills(rng, 1, offered, 4)
        if not batch:
            break
        picks.append({"type": "open_skill", "options": batch})
        offered.extend(str(spec.get("id")) for spec in batch)

    run.pending = (
        picks[0]
        if picks
        else {"type": "floor", "options": build_options(1, run.seed, cfg)}
    )
    run.pending["queue"] = picks[1:]
    return run


PERSISTENT_PENDING = (
    "enhance",
    "gear_enhance",
    "gear_stock",
    "proficiency",
    "skill_page",
)
"""挂在 pending 里但**跨层有效**的字段。

``refresh_floor`` 每次整份替换 pending，不保住这几个键的话：库存装备、
技能强化次数、熟练度都会在进下一层时凭空消失。

``forge`` 刻意不在其中：强化层是**本层**的界面，带进下一层会让
「离开」形同虚设，还会和商店会话抢同一个「离开」。
"""


def _carry_over(pending: dict[str, Any]) -> dict[str, Any]:
    """取出跨层字段，供新的 pending 继承。"""
    return {k: pending[k] for k in PERSISTENT_PENDING if k in pending}


def refresh_floor(run: RogueRun, cfg: dict[str, Any] | None = None) -> None:
    """把当前层的选项写进 pending（推进到新层后调用）。"""
    cfg = cfg or load_endless_config()
    run.region = region_of(run.floor, run.seed, cfg)
    carried = _carry_over(run.pending or {})
    run.pending = {"type": "floor", "options": build_options(run.floor, run.seed, cfg)}
    run.pending.update(carried)
    # 本层掉落的遗物/装备会改 hp/mp 上限，进新层时重算一次
    from .flow import sync_resources

    sync_resources(run)


def advance(run: RogueRun, cfg: dict[str, Any] | None = None) -> dict[str, int]:
    """前进一层并重建选项；跨阶段时回复 30% 上限。

    回血挂在这里而不是每层：设计定的是**阶段结束**才回一次。
    返回本次回血量（没跨界就是 0），供回执显示。
    """
    cfg = cfg or load_endless_config()
    before_stage = stage_of(run.floor)
    run.floor += 1
    refresh_floor(run, cfg)
    if stage_of(run.floor) != before_stage:
        from .flow import stage_recovery

        return stage_recovery(run, cfg)
    return {"hp": 0}


def pending_upgrades(run: RogueRun) -> int:
    """还有几次升级 4 选 1 没做。"""
    return pending_choices(run.progression)
