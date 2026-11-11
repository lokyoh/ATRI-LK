"""技能系统：收集池 + 装备槽、等级发放、轮回池与轮回保留。

数据模型（字段见 ``database.Pet``）
-----------------------------------
``pet.skills``
    已掌握的技能库（**收集池**）：``{"active": [...], "passive": [...]}``。无数量
    上限，只增不减；是否上阵由装备槽决定。
``pet.skill_equipped``
    **装备槽**：主动 / 被动各至多 ``limits.active_slots`` / ``passive_slots``
    （默认 4 + 4）。战斗与被动加成只结算这里。
``pet.skill_sources``
    技能 id → 来源（``starter`` / ``regular`` / ``reincarnation`` / ``rogue`` /
    ``legacy``）。来源由配置池归属推导（:func:`infer_source`），首次入池时落库，
    决定「轮回时保留什么」——只保留 :data:`RETAINED_SOURCES`。
``pet.skill_grants``
    等级里程碑的已发放计数 ``{"active": n, "passive": m}``，保证重复读档不重复发。

发放规则（``skills.yaml`` 的 ``grants`` 块）
-------------------------------------------
* **开局**：``pools.starter`` 的技能每世开局必得（与原版一致）。
* **等级**：每 ``active_every``（默认 5）级从 ``pools.regular.active`` 抽 1 个主动，
  每 ``passive_every``（默认 10）级抽 1 个被动。抽的是**尚未掌握**的技能，池子抽空
  即自然停发。里程碑按 ``pet.level // every`` 计算，所以一次升多级会补齐所有里程碑，
  旧存档首次读档也会按当前等级补齐。
* **轮回**：满级轮回时从 ``pools.reincarnation`` 额外抽 1 个（:func:`draw_reincarnation_skill`）。
* **肉鸽**：每个区域首次击败精英 / 首领的奖励技能由 ``roguelike.yaml`` 的
  ``stage_skill_rewards`` 指定，结算时经 :func:`grant_skill` 入池。

轮回保留
--------
:func:`apply_reincarnation_retention` 只保留来源为 ``reincarnation`` / ``rogue``
的技能（**轮回技能与奖励技能**），其余（开局 / 常规 / 旧存档 legacy）全部清空，
并尽量保留玩家原来的装备搭配，空位再自动补满。
"""

import random
from typing import Any

SLOT_TYPES = ("active", "passive")
"""两种技能类型，也是收集池 / 装备槽的两个键。"""

SLOT_LABELS = {"active": "主动", "passive": "被动"}

RETAINED_SOURCES = frozenset({"reincarnation", "rogue"})
"""轮回时保留的技能来源：轮回技能 + 肉鸽奖励技能。"""

SOURCE_LABELS = {
    "starter": "开局",
    "regular": "常规",
    "reincarnation": "轮回",
    "rogue": "奖励",
    "legacy": "旧档",
}


def _load_skills_config() -> dict[str, Any]:
    from ..config_loader import load_config

    return load_config("skills")


def _as_slot_map(value: Any) -> dict[str, list[str]]:
    """把任意脏数据规整成 ``{"active": [...], "passive": [...]}``。"""
    source = value if isinstance(value, dict) else {}
    return {
        slot: [str(item) for item in (source.get(slot) or []) if str(item)]
        for slot in SLOT_TYPES
    }


# ---------------------------------------------------------------------------
# 技能定义表
# ---------------------------------------------------------------------------


def skill_specs(skills_config: dict[str, Any] | None = None) -> dict[str, dict]:
    """技能定义表 ``{skill_id: spec}``（配置里是列表，这里转成 id 索引）。"""
    cfg = skills_config if skills_config is not None else _load_skills_config()
    raw = cfg.get("skills") or []
    items: list[dict] = []
    if isinstance(raw, dict):  # 兼容旧的「映射」写法
        items = [
            dict(spec, id=sid) for sid, spec in raw.items() if isinstance(spec, dict)
        ]
    else:
        items = [spec for spec in raw if isinstance(spec, dict)]
    specs: dict[str, dict] = {}
    for spec in items:
        skill_id = str(spec.get("id") or "").strip()
        if not skill_id:
            continue
        specs[skill_id] = spec
    return specs


def skill_spec(
    skill_id: str, skills_config: dict[str, Any] | None = None
) -> dict | None:
    """单个技能定义；不存在返回 ``None``。"""
    return skill_specs(skills_config).get(str(skill_id))


def skill_name(skill_id: str, skills_config: dict[str, Any] | None = None) -> str:
    """展示名；技能已从配置里删除时退回 id。"""
    spec = skill_spec(skill_id, skills_config) or {}
    return str(spec.get("name") or skill_id)


def skill_type(
    skill_id: str, skills_config: dict[str, Any] | None = None
) -> str | None:
    """技能类型（``active`` / ``passive``）；未知技能返回 ``None``。"""
    spec = skill_spec(skill_id, skills_config) or {}
    slot = spec.get("type")
    return slot if slot in SLOT_TYPES else None


def equipped_limit(slot_type: str, skills_config: dict[str, Any] | None = None) -> int:
    """某类型的装备槽数量（默认 4）。"""
    cfg = skills_config if skills_config is not None else _load_skills_config()
    limits = cfg.get("limits", {}) or {}
    try:
        return max(0, int(limits.get(f"{slot_type}_slots", 4)))
    except (TypeError, ValueError):
        return 4


# ---------------------------------------------------------------------------
# 池子与来源
# ---------------------------------------------------------------------------


def pool_ids(
    pool: str,
    slot_type: str | None = None,
    skills_config: dict[str, Any] | None = None,
) -> list[str]:
    """读取某个发放池的 id 列表。

    ``pool`` = ``starter`` / ``regular`` / ``reincarnation``；后两者按类型分组的，
    ``slot_type`` 为其一，省略时返回两类合并。
    """
    cfg = skills_config if skills_config is not None else _load_skills_config()
    node: Any = (cfg.get("pools") or {}).get(pool)
    if isinstance(node, list):
        return [str(item) for item in node]
    if not isinstance(node, dict):
        return []
    if slot_type is not None:
        return [str(item) for item in (node.get(slot_type) or [])]
    return [str(item) for slot in SLOT_TYPES for item in (node.get(slot) or [])]


def _source_pool_map(
    skills_config: dict[str, Any] | None = None,
) -> dict[str, str]:
    """``{skill_id: source}``：由池子归属推导的技能来源表。"""
    cfg = skills_config if skills_config is not None else _load_skills_config()
    mapping: dict[str, str] = {}
    # 先登记 starter：tackle 同时出现在 starter 与 regular 池里，先到先得，
    # 否则旧存档迁移出来的撞击会被误记成「常规」来源。
    for skill_id in pool_ids("starter", None, cfg):
        mapping.setdefault(skill_id, "starter")
    for skill_id in pool_ids("regular", None, cfg):
        mapping.setdefault(skill_id, "regular")
    for skill_id in pool_ids("reincarnation", None, cfg):
        mapping.setdefault(skill_id, "reincarnation")
    return mapping


def rogue_reward_skill_ids(
    rogue_config: dict[str, Any] | None = None,
) -> set[str]:
    """肉鸽阶段奖励技能的 id 集合（来源 ``rogue``）。"""
    if rogue_config is None:
        from .roguelike._common import _load_rogue_config

        rogue_config = _load_rogue_config()
    rewards = rogue_config.get("stage_skill_rewards") or {}
    ids: set[str] = set()
    for stage in rewards.values():
        if not isinstance(stage, dict):
            continue
        for key in ("elite", "boss"):
            skill_id = stage.get(key)
            if skill_id:
                ids.add(str(skill_id))
    return ids


def infer_source(skill_id: str, skills_config: dict[str, Any] | None = None) -> str:
    """推导技能来源；不属于任何池子的旧技能记为 ``legacy``。"""
    skill_id = str(skill_id)
    mapping = _source_pool_map(skills_config)
    if skill_id in mapping:
        return mapping[skill_id]
    if skill_id in rogue_reward_skill_ids():
        return "rogue"
    return "legacy"


def source_of(pet, skill_id: str, skills_config: dict[str, Any] | None = None) -> str:
    """宠物身上某个技能的来源：优先用落库记录，缺失时再推导。"""
    stored = getattr(pet, "skill_sources", None) or {}
    recorded = stored.get(str(skill_id))
    if recorded:
        return str(recorded)
    return infer_source(skill_id, skills_config)


# ---------------------------------------------------------------------------
# 收集池 / 装备槽
# ---------------------------------------------------------------------------


def ensure_skill_storage(pet, skills_config: dict[str, Any] | None = None) -> bool:
    """规整四个技能字段并修复历史数据，返回是否发生改动。

    修复内容：
    * 把 ``skills`` / ``skill_equipped`` 规整成两键字典；
    * 装备里的技能必须在收集池里（旧存档缺收集池时反推）；
    * 装备类型必须与技能定义一致、数量不得超过槽位上限；
    * 补齐 ``skill_sources`` / ``skill_grants``。
    """
    cfg = skills_config if skills_config is not None else _load_skills_config()
    specs = skill_specs(cfg)
    changed = False

    collection = _as_slot_map(getattr(pet, "skills", None))
    equipped = _as_slot_map(getattr(pet, "skill_equipped", None))
    sources = getattr(pet, "skill_sources", None)
    grants = getattr(pet, "skill_grants", None)

    if collection != getattr(pet, "skills", None):
        pet.skills = collection
        changed = True

    # 装备里的技能若不在收集池里，说明是旧存档/手改数据：补进收集池。
    for slot in SLOT_TYPES:
        for skill_id in equipped[slot]:
            if skill_id not in collection[slot]:
                collection[slot].append(skill_id)
                pet.skills = collection
                changed = True

    # 只保留定义存在、类型匹配、且在收集池里的装备；并裁剪到槽位上限。
    fixed_equipped: dict[str, list[str]] = {slot: [] for slot in SLOT_TYPES}
    for slot in SLOT_TYPES:
        limit = equipped_limit(slot, cfg)
        for skill_id in equipped[slot]:
            if len(fixed_equipped[slot]) >= limit:
                break
            if skill_id in fixed_equipped[slot]:
                continue
            if skill_id not in collection[slot]:
                continue
            declared = specs.get(skill_id, {}).get("type")
            if declared is not None and declared != slot:
                continue
            fixed_equipped[slot].append(skill_id)
    if fixed_equipped != getattr(pet, "skill_equipped", None):
        pet.skill_equipped = fixed_equipped
        changed = True

    new_sources = (
        {
            str(key): str(value)
            for key, value in (sources or {}).items()
            if isinstance(key, str) and isinstance(value, str)
        }
        if isinstance(sources, dict)
        else {}
    )
    all_known = [sid for slot in SLOT_TYPES for sid in collection[slot]]
    for skill_id in all_known:
        new_sources.setdefault(skill_id, infer_source(skill_id, cfg))
    if new_sources != getattr(pet, "skill_sources", None):
        pet.skill_sources = new_sources
        changed = True

    new_grants = {"active": 0, "passive": 0}
    if isinstance(grants, dict):
        for slot in SLOT_TYPES:
            try:
                new_grants[slot] = max(0, int(grants.get(slot, 0) or 0))
            except (TypeError, ValueError):
                new_grants[slot] = 0
    if new_grants != getattr(pet, "skill_grants", None):
        pet.skill_grants = new_grants
        changed = True

    return changed


def known_skills(pet, slot_type: str | None = None) -> list[str] | dict[str, list[str]]:
    """收集池里的技能；``slot_type`` 省略时返回按类型分组的字典。"""
    collection = _as_slot_map(getattr(pet, "skills", None))
    if slot_type is None:
        return collection
    return list(collection.get(slot_type, []))


def equipped_skills(
    pet, slot_type: str | None = None
) -> list[str] | dict[str, list[str]]:
    """当前装备的技能；``slot_type`` 省略时返回按类型分组的字典。"""
    equipped = _as_slot_map(getattr(pet, "skill_equipped", None))
    if slot_type is None:
        return equipped
    return list(equipped.get(slot_type, []))


def is_known(pet, skill_id: str) -> bool:
    """宠物是否已掌握该技能（不论是否装备）。"""
    collection = _as_slot_map(getattr(pet, "skills", None))
    return any(str(skill_id) in collection[slot] for slot in SLOT_TYPES)


def is_equipped(pet, skill_id: str) -> bool:
    """宠物是否装备着该技能。"""
    equipped = _as_slot_map(getattr(pet, "skill_equipped", None))
    return any(str(skill_id) in equipped[slot] for slot in SLOT_TYPES)


def _equip_raw(pet, slot: str, skill_id: str, skills_config: dict) -> bool:
    """不做校验地把技能塞进槽位（仅供内部补位使用）。"""
    equipped = _as_slot_map(getattr(pet, "skill_equipped", None))
    if skill_id in equipped[slot]:
        return False
    if len(equipped[slot]) >= equipped_limit(slot, skills_config):
        return False
    equipped[slot].append(skill_id)
    pet.skill_equipped = equipped
    return True


def equip_skill(pet, skill_id: str, skills_config: dict[str, Any] | None = None) -> str:
    """装备一个已掌握的技能；返回技能 id。失败抛 ``ValueError``。"""
    cfg = skills_config if skills_config is not None else _load_skills_config()
    ensure_skill_storage(pet, cfg)
    skill_id = str(skill_id)
    spec = skill_spec(skill_id, cfg)
    if spec is None:
        raise ValueError(f"技能不存在：{skill_id}")
    slot = spec.get("type")
    if slot not in SLOT_TYPES:
        raise ValueError(f"技能类型无效：{skill_id}")
    if not is_known(pet, skill_id):
        raise ValueError(f"还没有掌握「{skill_name(skill_id, cfg)}」，不能装备")
    if is_equipped(pet, skill_id):
        raise ValueError(f"「{skill_name(skill_id, cfg)}」已经装备着了")

    limit = equipped_limit(slot, cfg)
    used = len(equipped_skills(pet, slot))
    if used >= limit:
        label = SLOT_LABELS.get(slot, slot)
        raise ValueError(
            f"{label}技能槽已满（{used}/{limit}），请先卸下一个{label}技能"
        )
    if not _equip_raw(pet, slot, skill_id, cfg):
        raise ValueError("装备失败，请稍后再试")
    return skill_id


def unequip_skill(
    pet, skill_id: str, skills_config: dict[str, Any] | None = None
) -> str:
    """卸下一个装备中的技能（技能仍留在收集池里）；返回技能 id。"""
    cfg = skills_config if skills_config is not None else _load_skills_config()
    ensure_skill_storage(pet, cfg)
    skill_id = str(skill_id)
    equipped = _as_slot_map(getattr(pet, "skill_equipped", None))
    for slot in SLOT_TYPES:
        if skill_id in equipped[slot]:
            equipped[slot].remove(skill_id)
            pet.skill_equipped = equipped
            return skill_id
    raise ValueError(f"「{skill_name(skill_id, cfg)}」没有装备着")


def learn_skill(
    pet,
    skill_id: str,
    skills_config: dict[str, Any] | None = None,
    *,
    source: str | None = None,
    auto_equip: bool = True,
) -> bool:
    """把技能加入收集池（已掌握则不重复），并按需自动装备。返回是否新增。"""
    cfg = skills_config if skills_config is not None else _load_skills_config()
    ensure_skill_storage(pet, cfg)
    skill_id = str(skill_id)
    spec = skill_spec(skill_id, cfg)
    if spec is None:
        return False
    slot = spec.get("type")
    if slot not in SLOT_TYPES:
        return False
    if is_known(pet, skill_id):
        return False

    collection = _as_slot_map(getattr(pet, "skills", None))
    collection[slot].append(skill_id)
    pet.skills = collection

    sources = dict(getattr(pet, "skill_sources", None) or {})
    sources[skill_id] = source or infer_source(skill_id, cfg)
    pet.skill_sources = sources

    proficiency = dict(getattr(pet, "skill_proficiency", None) or {})
    proficiency.setdefault(skill_id, 0.0)
    pet.skill_proficiency = proficiency

    if auto_equip:
        _equip_raw(pet, slot, skill_id, cfg)
    return True


def grant_skill(
    pet,
    skill_id: str,
    skills_config: dict[str, Any] | None = None,
    *,
    source: str | None = None,
) -> dict[str, Any]:
    """发放一个技能（加入收集池，有空槽则自动装备）。

    返回 ``{"id", "name", "source", "added", "equipped"}``；``added`` 为
    ``False`` 表示此前就已掌握。
    """
    cfg = skills_config if skills_config is not None else _load_skills_config()
    added = learn_skill(pet, skill_id, cfg, source=source, auto_equip=True)
    return {
        "id": str(skill_id),
        "name": skill_name(skill_id, cfg),
        "source": source_of(pet, skill_id, cfg),
        "added": added,
        "equipped": is_equipped(pet, skill_id),
    }


def forget_skill(
    pet, skill_id: str, skills_config: dict[str, Any] | None = None
) -> bool:
    """从收集池与装备槽里彻底移除一个技能（熟练度一并清理）。"""
    cfg = skills_config if skills_config is not None else _load_skills_config()
    ensure_skill_storage(pet, cfg)
    skill_id = str(skill_id)
    removed = False
    collection = _as_slot_map(getattr(pet, "skills", None))
    for slot in SLOT_TYPES:
        if skill_id in collection[slot]:
            collection[slot].remove(skill_id)
            removed = True
    pet.skills = collection

    equipped = _as_slot_map(getattr(pet, "skill_equipped", None))
    for slot in SLOT_TYPES:
        if skill_id in equipped[slot]:
            equipped[slot].remove(skill_id)
            removed = True
    pet.skill_equipped = equipped

    sources = dict(getattr(pet, "skill_sources", None) or {})
    sources.pop(skill_id, None)
    pet.skill_sources = sources

    proficiency = dict(getattr(pet, "skill_proficiency", None) or {})
    proficiency.pop(skill_id, None)
    pet.skill_proficiency = proficiency
    return removed


# ---------------------------------------------------------------------------
# 发放：开局 / 等级 / 轮回
# ---------------------------------------------------------------------------


def _draw_from_pool(
    pet,
    cfg: dict[str, Any],
    pool: str,
    slot_type: str,
    rng: random.Random | Any | None = None,
) -> str | None:
    """从未掌握的技能里随机抽一个；池子抽空返回 ``None``。"""
    specs = skill_specs(cfg)
    known = set(known_skills(pet, slot_type))
    candidates = [
        skill_id
        for skill_id in pool_ids(pool, slot_type, cfg)
        if skill_id in specs
        and skill_id not in known
        and specs[skill_id].get("type") == slot_type
    ]
    if not candidates:
        return None
    return (rng or random).choice(candidates)


def grant_start_skills(
    pet, skills_config: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """发放开局技能（``pools.starter``）；已掌握的跳过。"""
    cfg = skills_config if skills_config is not None else _load_skills_config()
    granted = []
    for skill_id in pool_ids("starter", None, cfg):
        if is_known(pet, skill_id):
            continue
        result = grant_skill(pet, skill_id, cfg, source="starter")
        if result["added"]:
            granted.append(result)
    return granted


def grant_level_skills(
    pet,
    skills_config: dict[str, Any] | None = None,
    *,
    rng: random.Random | Any | None = None,
) -> list[dict[str, Any]]:
    """按等级里程碑补发常规池技能。

    每 ``active_every`` 级 1 个主动、每 ``passive_every`` 级 1 个被动，
    用 ``pet.skill_grants`` 记录已发放次数，因此可以反复调用而无副作用。
    里程碑一旦到达就视为消耗（池子抽空时不再重试）。
    """
    cfg = skills_config if skills_config is not None else _load_skills_config()
    ensure_skill_storage(pet, cfg)
    grants_cfg = cfg.get("grants", {}) or {}
    counters = dict(getattr(pet, "skill_grants", None) or {})
    counters.setdefault("active", 0)
    counters.setdefault("passive", 0)

    granted: list[dict[str, Any]] = []
    for slot_type, every_key in (
        ("active", "active_every"),
        ("passive", "passive_every"),
    ):
        try:
            every = int(grants_cfg.get(every_key, 0) or 0)
        except (TypeError, ValueError):
            every = 0
        if every <= 0:
            continue
        target = max(0, int(pet.level)) // every
        have = int(counters.get(slot_type, 0) or 0)
        while have < target:
            have += 1
            counters[slot_type] = have
            skill_id = _draw_from_pool(pet, cfg, "regular", slot_type, rng)
            if skill_id is None:
                continue
            result = grant_skill(pet, skill_id, cfg, source="regular")
            if result["added"]:
                granted.append(result)
    pet.skill_grants = counters
    return granted


def draw_reincarnation_skill(
    pet,
    skills_config: dict[str, Any] | None = None,
    *,
    rng: random.Random | Any | None = None,
) -> dict[str, Any] | None:
    """从轮回池额外抽取 1 个技能（满级轮回时调用）；池子抽空返回 ``None``。"""
    cfg = skills_config if skills_config is not None else _load_skills_config()
    ensure_skill_storage(pet, cfg)
    specs = skill_specs(cfg)
    known = {sid for slot in SLOT_TYPES for sid in known_skills(pet, slot)}
    candidates = [
        skill_id
        for skill_id in pool_ids("reincarnation", None, cfg)
        if skill_id in specs and skill_id not in known
    ]
    if not candidates:
        return None
    skill_id = (rng or random).choice(candidates)
    return grant_skill(pet, skill_id, cfg, source="reincarnation")


def reincarnation_draw_count(
    skills_config: dict[str, Any] | None = None,
) -> int:
    """满级轮回额外抽取的技能数（``grants.reincarnation_draw``，默认 1）。"""
    cfg = skills_config if skills_config is not None else _load_skills_config()
    grants_cfg = cfg.get("grants", {}) or {}
    try:
        return max(0, int(grants_cfg.get("reincarnation_draw", 1) or 0))
    except (TypeError, ValueError):
        return 1


def draw_reincarnation_skills(
    pet,
    skills_config: dict[str, Any] | None = None,
    *,
    count: int | None = None,
    rng: random.Random | Any | None = None,
) -> list[dict[str, Any]]:
    """按 ``grants.reincarnation_draw`` 抽若干个轮回技能；池子抽空即停止。"""
    cfg = skills_config if skills_config is not None else _load_skills_config()
    draws = reincarnation_draw_count(cfg) if count is None else max(0, int(count))
    drawn: list[dict[str, Any]] = []
    for _ in range(draws):
        result = draw_reincarnation_skill(pet, cfg, rng=rng)
        if result is None:
            break
        drawn.append(result)
    return drawn


def retention_skill_ids(
    pet, skills_config: dict[str, Any] | None = None
) -> dict[str, list[str]]:
    """轮回时会保留下来的技能（按类型分组），不修改宠物。"""
    cfg = skills_config if skills_config is not None else _load_skills_config()
    kept: dict[str, list[str]] = {}
    for slot in SLOT_TYPES:
        kept[slot] = [
            skill_id
            for skill_id in known_skills(pet, slot)
            if source_of(pet, skill_id, cfg) in RETAINED_SOURCES
        ]
    return kept


def apply_reincarnation_retention(
    pet, skills_config: dict[str, Any] | None = None
) -> dict[str, list[str]]:
    """轮回时裁剪技能：**只保留轮回技能与奖励技能**。

    会顺手清理熟练度、把里程碑计数归零（新的一世重新累计），并优先保留玩家
    原来的装备搭配，空位再按收集池顺序自动补满。返回保留下来的技能（按类型分组）。
    """
    cfg = skills_config if skills_config is not None else _load_skills_config()
    ensure_skill_storage(pet, cfg)

    previous = _as_slot_map(getattr(pet, "skill_equipped", None))
    kept = retention_skill_ids(pet, cfg)

    pet.skills = {slot: list(kept[slot]) for slot in SLOT_TYPES}
    pet.skill_equipped = {slot: [] for slot in SLOT_TYPES}
    pet.skill_sources = {
        skill_id: source_of(pet, skill_id, cfg)
        for slot in SLOT_TYPES
        for skill_id in kept[slot]
    }
    all_kept = {sid for slot in SLOT_TYPES for sid in kept[slot]}
    pet.skill_proficiency = {
        skill_id: float(value)
        for skill_id, value in (getattr(pet, "skill_proficiency", None) or {}).items()
        if skill_id in all_kept
    }
    pet.skill_grants = dict.fromkeys(SLOT_TYPES, 0)

    for slot in SLOT_TYPES:
        for skill_id in previous[slot]:
            if skill_id in kept[slot]:
                _equip_raw(pet, slot, skill_id, cfg)
        for skill_id in kept[slot]:
            _equip_raw(pet, slot, skill_id, cfg)
    return kept


# ---------------------------------------------------------------------------
# 名称解析与展示辅助
# ---------------------------------------------------------------------------


def resolve_skill(value: str, skills_config: dict[str, Any] | None = None) -> str:
    """把玩家输入的「技能名 / id」解析成技能 id。

    与性格、天赋同惯例：展示用中文名，落库用 id，输入两者都认（忽略大小写）。
    """
    specs = skill_specs(skills_config)
    wanted = (value or "").strip()
    if not wanted:
        raise ValueError("请提供技能名或 id")
    lowered = wanted.lower()
    for skill_id, spec in specs.items():
        candidates = {skill_id.lower(), str(spec.get("name") or "").strip().lower()}
        if lowered in candidates:
            return skill_id
    raise ValueError(f"技能不存在：{wanted}")


# ---------------------------------------------------------------------------
# 战斗接口（只认装备中的技能）
# ---------------------------------------------------------------------------


def equipped_specs(
    pet, slot_type: str, skills_config: dict[str, Any] | None = None
) -> list[tuple[str, dict]]:
    """装备中且定义有效的技能 ``[(id, spec), ...]``。"""
    cfg = skills_config if skills_config is not None else _load_skills_config()
    specs = skill_specs(cfg)
    pairs: list[tuple[str, dict]] = []
    for skill_id in equipped_skills(pet, slot_type):
        spec = specs.get(skill_id)
        if spec is None:
            continue
        if spec.get("type") not in (None, slot_type):
            continue
        pairs.append((skill_id, spec))
    return pairs


def get_skill_power(
    pet,
    skill_id: str,
    skills_config: dict[str, Any] | None = None,
) -> float:
    """技能威力（含熟练度加成）。"""
    cfg = skills_config if skills_config is not None else _load_skills_config()
    spec = skill_spec(skill_id, cfg)
    if spec is None:
        raise ValueError(f"技能不存在：{skill_id}")

    limits = cfg.get("limits", {}) or {}
    maximum = float(limits.get("proficiency_max", 1.0))
    bonus = float(limits.get("proficiency_power_bonus", 0.3))
    proficiency = min(
        maximum,
        max(
            0.0,
            float((getattr(pet, "skill_proficiency", None) or {}).get(skill_id, 0.0)),
        ),
    )
    return float(spec.get("power", 0)) * (1.0 + proficiency * bonus)


def increase_proficiency(
    pet,
    skill_id: str,
    amount: float | None = None,
    skills_config: dict[str, Any] | None = None,
) -> None:
    """施放技能时涨熟练度（只有已掌握的技能才计）。"""
    if not is_known(pet, skill_id):
        return
    cfg = skills_config if skills_config is not None else _load_skills_config()
    limits = cfg.get("limits", {}) or {}
    gain = float(limits.get("proficiency_gain", 0.01)) if amount is None else amount
    maximum = float(limits.get("proficiency_max", 1.0))
    proficiency = dict(getattr(pet, "skill_proficiency", None) or {})
    current = float(proficiency.get(skill_id, 0.0))
    proficiency[skill_id] = min(maximum, max(0.0, current + gain))
    pet.skill_proficiency = proficiency


def passive_bonuses(
    pet, skills_config: dict[str, Any] | None = None
) -> dict[str, float]:
    """汇总**装备中**被动技能的加成（键见 ``skills.yaml`` 的被动说明）。"""
    from .roguelike._common import PASSIVE_STAT_KEYS

    cfg = skills_config if skills_config is not None else _load_skills_config()
    total: dict[str, float] = dict.fromkeys(PASSIVE_STAT_KEYS, 0.0)
    for _, spec in equipped_specs(pet, "passive", cfg):
        for key, value in (spec.get("effect") or {}).items():
            if key in total and isinstance(value, int | float):
                total[key] += float(value)
    return total
