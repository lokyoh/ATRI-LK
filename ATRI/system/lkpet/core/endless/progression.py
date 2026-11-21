"""无尽模式的等级与经济。

经验够了自动升级，每次升级 4 选 1（攻 / 防 / 血 / 蓝），给固定值。
速度不在可选项里：它是 spd_exponent=0.5 的开方属性，放开点选会叠过头。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import load_endless_config

LEVEL_KEYS: tuple[str, ...] = ("atk", "def", "hp", "mp")
"""4 选 1 的四个维度。顺序即展示顺序。"""

LEVEL_LABELS = {"atk": "攻", "def": "防", "hp": "血", "mp": "蓝"}


@dataclass
class Progression:
    """局内进度：等级、经验、已累积的属性加成。"""

    level: int = 1
    exp: int = 0
    exp_next: int = 0
    bonuses: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(LEVEL_KEYS, 0)
    )
    pending: list[str] = field(default_factory=list)
    """待选的升级项：经验溢出时可能连升多级，逐级弹出 4 选 1。"""

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "exp": self.exp,
            "exp_next": self.exp_next,
            "bonuses": dict(self.bonuses),
            "pending": list(self.pending),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Progression:
        if not data:
            return cls()
        bonuses = dict.fromkeys(LEVEL_KEYS, 0)
        for key, value in (data.get("bonuses") or {}).items():
            if key in bonuses:
                bonuses[key] = int(value)
        return cls(
            level=int(data.get("level", 1) or 1),
            exp=int(data.get("exp", 0) or 0),
            exp_next=int(data.get("exp_next", 0) or 0),
            bonuses=bonuses,
            pending=[str(x) for x in (data.get("pending") or [])],
        )


def exp_threshold(level: int, cfg: dict[str, Any] | None = None) -> int:
    """升到下一级所需经验 = base + per_level × 当前等级。"""
    cfg = cfg or load_endless_config()
    node = (cfg.get("leveling") or {}).get("exp_to_next") or {}
    base = int(node.get("base", 50) or 50)
    per = int(node.get("per_level", 30) or 30)
    return max(1, base + per * max(1, int(level)))


def gain_table(cfg: dict[str, Any] | None = None) -> dict[str, int]:
    """每个可选项对应的固定增加值。"""
    cfg = cfg or load_endless_config()
    node = (cfg.get("leveling") or {}).get("gain") or {}
    return {k: int(node.get(k, 0) or 0) for k in LEVEL_KEYS}


def new_progression(cfg: dict[str, Any] | None = None) -> Progression:
    """开局进度：1 级、0 经验、0 加成。"""
    prog = Progression(level=1, exp=0)
    prog.exp_next = exp_threshold(prog.level, cfg)
    return prog


def award_exp(prog: Progression, amount: int, cfg: dict[str, Any] | None = None) -> int:
    """加经验并推进等级；返回本次升了几级。

    升级不立刻应用加成：溢出时把升级次数记进 pending，
    由外层逐级弹出 4 选 1。
    """
    if amount <= 0:
        return 0
    if prog.exp_next <= 0:
        prog.exp_next = exp_threshold(prog.level, cfg)
    prog.exp += int(amount)
    gained = 0
    while prog.exp >= prog.exp_next:
        prog.exp -= prog.exp_next
        prog.level += 1
        gained += 1
        prog.pending.append("")
        prog.exp_next = exp_threshold(prog.level, cfg)
    return gained


def apply_choice(
    prog: Progression, key: str, cfg: dict[str, Any] | None = None
) -> bool:
    """应用一次 4 选 1；非法键或没有待选项时返回 False。"""
    if key not in LEVEL_KEYS or not prog.pending:
        return False
    prog.pending.pop(0)
    prog.bonuses[key] = int(prog.bonuses.get(key, 0)) + gain_table(cfg)[key]
    return True


def pending_choices(prog: Progression) -> int:
    """还剩几次 4 选 1。"""
    return len(prog.pending)


def combat_attributes(
    base: dict[str, int],
    prog: Progression,
) -> dict[str, int]:
    """模板面板 + 升级加成，得到战斗用的属性表。"""
    out = {str(k): int(v) for k, v in base.items()}
    for key in LEVEL_KEYS:
        if key in out:
            out[key] += int(prog.bonuses.get(key, 0))
    return out
