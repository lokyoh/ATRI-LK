"""无尽模式的配置读取。

只读 ``endless.yaml```，与爬塔的 roguelike.yaml 完全不共享。
"""

from __future__ import annotations

from typing import Any


def load_endless_config() -> dict[str, Any]:
    """无尽配置 + 爬塔的**敌人侧**定义表。

    怪物池里每只怪带 ``skills``/``preset``/``passive``，这些 id 指向
    爬塔 ``roguelike.yaml`` 的 ``enemy_skills``/``enemy_phases``/``elite_passives``。
    无尽模式必须复用它们——否则敌方技能全部被 ``_unit_skills`` 过滤掉，
    「蓄力大招」「召唤」这类规则型词缀也就无从触发。

    只在无尽配置缺这些块时合并，已写则尊重无尽自己的值。
    """
    from ...config_loader import load_config

    cfg = load_config("endless")
    rogue = load_config("roguelike")
    for key in (
        "enemy_skills",
        "enemy_phases",
        "elite_passives",
        "monster_traits",
        "monster_presets",
    ):
        if key not in cfg and key in rogue:
            cfg[key] = rogue[key]
    return cfg


def template_attributes(cfg: dict[str, Any] | None = None) -> dict[str, int]:
    """统一模板面板（hp/atk/def/spd/mp）。"""
    node = (cfg or load_endless_config()).get("template") or {}
    raw = node.get("attributes") or {}
    return {str(k): int(v) for k, v in raw.items()}


def template_level(cfg: dict[str, Any] | None = None) -> int:
    node = (cfg or load_endless_config()).get("template") or {}
    return max(1, int(node.get("level", 1) or 1))


def battle_cfg(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    return (cfg or load_endless_config()).get("battle") or {}


def balance_cfg(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """数值配平开关。整段缺失时返回空字典，调用方各自用自己的默认值。"""
    return (cfg or load_endless_config()).get("balance") or {}


def recovery_cfg(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """每层回复比例（hp_pct / mp_pct）。"""
    return balance_cfg(cfg).get("stage_recovery") or {}


def enemy_scale_cfg(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """敌人系数（按模板缩放，不按玩家当前面板）。"""
    return balance_cfg(cfg).get("enemy") or {}


def weaken_k(cfg: dict[str, Any] | None = None) -> float:
    """无尽削弱系数 K（HP / ATK / DEF 三项乘它）。"""
    return float(balance_cfg(cfg).get("k", 0.5) or 0.5)
