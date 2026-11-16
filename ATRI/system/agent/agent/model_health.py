"""Agent 模型健康检查：只检查与记日志，不发起调用。"""

from __future__ import annotations

from dataclasses import dataclass

from ..llm import SLOT_SPECS, SLOTS, llm_manager
from ..utils import log

#: 健康分组：WebUI 按组展示。
GROUP_DIALOG = "dialog"
GROUP_FEATURE = "feature"
GROUP_OPTIONAL = "optional"

GROUP_LABELS = {
    GROUP_DIALOG: "对话必需",
    GROUP_FEATURE: "功能必需",
    GROUP_OPTIONAL: "可选",
}

#: 使用点 → 分组；缺省算「功能」。
GROUP_BY_SLOT: dict[str, str] = {
    "think": GROUP_DIALOG,
    "chat": GROUP_DIALOG,
    "judge": GROUP_FEATURE,
    "memory": GROUP_FEATURE,
    "profile": GROUP_FEATURE,
    "schedule": GROUP_FEATURE,
    "outfit": GROUP_FEATURE,
    "vision": GROUP_FEATURE,
    "embedding": GROUP_FEATURE,
    "home": GROUP_OPTIONAL,
    "summary": GROUP_OPTIONAL,
    "tts": GROUP_OPTIONAL,
}

#: 缺少它直接拒绝整轮聊天；聊天缺失只降级。
REQUIRED_SLOTS: frozenset[str] = frozenset({"think"})


@dataclass(frozen=True)
class TypeSpec:
    """一个使用点的健康规格。"""

    slot: str
    label: str
    group: str
    required: bool

    @property
    def type(self) -> str:
        return self.slot


TYPE_SPECS: tuple[TypeSpec, ...] = tuple(
    TypeSpec(
        spec.key,
        spec.label,
        GROUP_BY_SLOT.get(spec.key, GROUP_FEATURE),
        spec.key in REQUIRED_SLOTS,
    )
    for spec in SLOT_SPECS
)

TYPE_LABELS: dict[str, str] = {spec.slot: spec.label for spec in TYPE_SPECS}


@dataclass
class TypeHealth:
    slot: str
    label: str
    group: str
    required: bool
    registered: list[str]
    preferred: str
    ok: bool
    reason: str

    @property
    def type_key(self) -> str:
        return self.slot


def check_agent_model_health() -> list[TypeHealth]:
    """检查每个使用点能否拿到模型。"""
    health: list[TypeHealth] = []
    for spec in TYPE_SPECS:
        slot_spec = SLOTS[spec.slot]
        capability = llm_manager.capability_for_slot(spec.slot)
        registered = (
            [m.name for m in llm_manager.get_models(capability)] if capability else []
        )
        preferred = llm_manager.get_preferred(spec.slot)
        ok = bool(registered)
        reason = "" if ok else f"没有可用的{'/'.join(slot_spec.capabilities)}模型"
        if ok and preferred and not llm_manager.model_fits_slot(preferred, spec.slot):
            ok = False
            reason = f"指定的模型 '{preferred}' 不具备该能力"
        health.append(
            TypeHealth(
                slot=spec.slot,
                label=spec.label,
                group=spec.group,
                required=spec.required,
                registered=registered,
                preferred=preferred,
                ok=ok,
                reason=reason,
            )
        )
    return health


def missing_required(health: list[TypeHealth] | None = None) -> list[TypeHealth]:
    return [
        item
        for item in (health or check_agent_model_health())
        if not item.ok and item.required
    ]


def _slot_health(health: list[TypeHealth] | None, slot: str) -> TypeHealth | None:
    items = health or check_agent_model_health()
    return next((item for item in items if item.slot == slot), None)


def can_think(health: list[TypeHealth] | None = None) -> bool:
    """思考链路是否可用；聊天入口只看这一个。"""
    item = _slot_health(health, "think")
    return bool(item and item.ok)


def can_chat(health: list[TypeHealth] | None = None) -> bool:
    item = _slot_health(health, "chat")
    return bool(item and item.ok)


def log_health(health: list[TypeHealth] | None = None) -> None:
    """把缺失的使用点写进日志：必需 error，其余 warning。"""
    items = health or check_agent_model_health()
    for item in items:
        if item.ok:
            continue
        message = f"Agent 模型 {item.label}（使用点 {item.slot}）不可用：{item.reason}"
        if item.required:
            log.error(message)
        else:
            log.warning(message)
    missing = [item for item in items if item.required and not item.ok]
    if missing:
        names = "、".join(f"{item.label}({item.slot})" for item in missing)
        log.error(
            f"Agent 缺少必需模型：{names}。"
            "请在 WebUI 的「AI模型设置」中为对应使用点选择模型。"
        )


def health_snapshot(health: list[TypeHealth] | None = None) -> dict:
    """给 WebUI / API 的快照。"""
    items = health or check_agent_model_health()
    groups: dict[str, list[dict]] = {key: [] for key in GROUP_LABELS}
    for item in items:
        groups.setdefault(item.group, []).append(
            {
                "type": item.slot,
                "label": item.label,
                "required": item.required,
                "ok": item.ok,
                "registered": item.registered,
                "preferred": item.preferred,
                "reason": item.reason,
            }
        )
    return {
        "ok": all(item.ok for item in items if item.required),
        "missingRequired": [
            item.slot for item in items if item.required and not item.ok
        ],
        "groups": [
            {
                "key": key,
                "label": GROUP_LABELS[key],
                "items": value,
            }
            for key, value in groups.items()
        ],
    }


__all__ = [
    "GROUP_DIALOG",
    "GROUP_FEATURE",
    "GROUP_LABELS",
    "GROUP_OPTIONAL",
    "REQUIRED_SLOTS",
    "TYPE_LABELS",
    "TYPE_SPECS",
    "TypeHealth",
    "TypeSpec",
    "can_chat",
    "can_think",
    "check_agent_model_health",
    "health_snapshot",
    "log_health",
    "missing_required",
]
