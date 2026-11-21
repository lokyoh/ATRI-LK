"""无尽模式的图鉴：记录、合并、结算。

四个图鉴列：

    CODEX_RELICS   ["relic_id", ...]
    CODEX_SKILLS   {"原型 id": 见过的最高等阶}
    CODEX_EQUIPS   ["gear_id", ...]
    CODEX_EVENTS   ["event_id", ...]

技能按**原型**记而不是按实例：160 个技能是 32 原型 × 5 等阶，
按实例记会变成 160 条、其中 5 条信息量重复；按原型只有 32 条，「练到几阶」更准确。

**不碰养成数据**：本模块只写 PETENDLESS 的四列与最高层，
绝不写成就点、宠物经验、装备或称号。
"""

from __future__ import annotations

from typing import Any

from .skills import skill_specs


def skill_archetype(skill_id: str) -> str:
    """``sk_<元素>_<系列>_<等阶>`` → ``<元素>_<系列>``（原型键）。

    id 不由本模块拼：直接从定义里取 ``element`` 与 ``series`` 更可靠，
    这个函数只是解析兜底，用于数据缺失时。
    """
    parts = str(skill_id).split("_")
    if len(parts) >= 4 and parts[0] == "sk":
        return f"{parts[1]}_{parts[2]}"
    return str(skill_id)


def merge_codex(
    old: Any,
    new: Any,
    *,
    kind: str = "list",
) -> Any:
    """合并两批图鉴数据；``kind`` 为 list 或 dict。"""
    if kind == "dict":
        out: dict[str, int] = {}
        for source in (old, new):
            for key, value in (source or {}).items():
                k = str(key)
                v = int(value or 0)
                out[k] = max(out.get(k, 0), v)
        return out
    merged = {str(x) for x in (old or [])} | {str(x) for x in (new or [])}
    return sorted(merged)


def add_relics(codex: list[str] | None, run_relics: dict[str, int] | None) -> list[str]:
    """把本局持有的遗物并入图鉴。"""
    ids = [str(k) for k, v in (run_relics or {}).items() if int(v) > 0]
    return merge_codex(codex, ids)


def add_skills(
    codex: dict[str, int] | None,
    equipped: list[str] | None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, int]:
    """把技能并入图鉴：按原型记最高等阶。"""
    specs = skill_specs(cfg)
    new: dict[str, int] = {}
    for skill_id in list(equipped or []):
        spec = specs.get(str(skill_id))
        if not spec:
            continue
        arch = f"{spec.get('element')}_{spec.get('series')}"
        tier = int(spec.get("tier", 1) or 1)
        new[arch] = max(new.get(arch, 0), tier)
    return merge_codex(codex, new, kind="dict")


def add_gear(
    codex: list[str] | None, equipped: dict[str, list[str]] | None
) -> list[str]:
    """把装备并入图鉴。

    槽位值必须是列表：早期反序列化 bug 会把列表变成字符串，
    逐字符并进图鉴后不可逆（merge 是并集）。这里显式跳过非列表值。
    """
    ids: list[str] = []
    for value in (equipped or {}).values():
        if not isinstance(value, list | tuple):
            continue
        ids.extend(str(g) for g in value)
    return merge_codex(codex, ids)


def add_events(codex: list[str] | None, seen: list[str] | None) -> list[str]:
    return merge_codex(codex, seen)


def sync_codex(endless: Any, run: Any) -> dict[str, Any]:
    """把本局进度写进图鉴，**不涨场次**。

    刻意拆出来：一局中途死掉、或玩家直接关掉对话时走不到「撤退」，
    但图鉴该记的还是要记。merge 是并集/取大，重复调用结果不变。
    """
    floor = int(getattr(run, "floor", 0) or 0)
    before_best = int(getattr(endless, "best_floor", 0) or 0)
    endless.best_floor = max(before_best, floor)
    relics = getattr(run, "relics", None)
    owned = (
        {str(r): 1 for r in (relics or [])}
        if isinstance(relics, list)
        else dict(relics or {})
    )
    endless.codex_relics = add_relics(getattr(endless, "codex_relics", None), owned)

    equipped: list[str] = []
    skills = getattr(run, "skills", None)
    if isinstance(skills, dict):
        equipped = [str(s) for ids in skills.values() for s in (ids or [])]
    elif isinstance(skills, list):
        equipped = [str(s) for s in skills]
    endless.codex_skills = add_skills(getattr(endless, "codex_skills", None), equipped)

    endless.codex_equips = add_gear(
        getattr(endless, "codex_equips", None), getattr(run, "equip", None)
    )
    endless.codex_events = add_events(
        getattr(endless, "codex_events", None), getattr(run, "seen_events", None)
    )

    return {
        "floor": floor,
        "best": endless.best_floor,
        "new_best": floor > before_best,
        "runs": endless.total_runs,
        "relics": len(endless.codex_relics),
        "skills": len(endless.codex_skills),
        "equips": len(endless.codex_equips),
        "events": len(endless.codex_events),
    }


def settle(endless: Any, run: Any) -> dict[str, Any]:
    """结算一局：sync_codex + 场次 +1。"""
    summary = sync_codex(endless, run)
    endless.total_runs = int(getattr(endless, "total_runs", 0) or 0) + 1
    summary["runs"] = endless.total_runs
    return summary


def codex_paths(endless: Any) -> dict[str, Any]:
    """图鉴进度（供「进度」回执）：已收集数与全集数。"""
    from .gear import gear_list
    from .relics import relic_list
    from .skills import ELEMENTS, SERIES

    relics_total = len(relic_list())
    equips_total = len(gear_list())
    skills_total = len(ELEMENTS) * len(SERIES)
    return {
        "relics": (len(getattr(endless, "codex_relics", []) or []), relics_total),
        "skills": (len(getattr(endless, "codex_skills", {}) or {}), skills_total),
        "equips": (len(getattr(endless, "codex_equips", []) or []), equips_total),
        "events": len(getattr(endless, "codex_events", []) or []),
    }
