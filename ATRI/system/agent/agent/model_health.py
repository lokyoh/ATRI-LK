"""Agent 模型类型健康检查。

Agent 只认 ``atri-*`` 类型，并且**绝不回退**到通用 ``chat`` / ``tool``。
因此「某个类型没配」必须是一个显式的、可解释的状态，而不是悄悄
换一个模型继续跑。

本模块只做检查与日志，不发起模型调用；启动、配置变更、聊天入口、
WebUI 都复用同一份结果，避免四处各写一遍判断。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..llm import ModelType, llm_manager
from ..utils import log

#: 健康分组：WebUI 按组展示，避免 12 个类型平铺吓人。
GROUP_DIALOG = "dialog"
GROUP_FEATURE = "feature"
GROUP_OPTIONAL = "optional"

GROUP_LABELS = {
    GROUP_DIALOG: "对话必需",
    GROUP_FEATURE: "功能必需",
    GROUP_OPTIONAL: "可选",
}


@dataclass(frozen=True)
class TypeSpec:
    type: ModelType
    label: str
    group: str
    required: bool


TYPE_SPECS: tuple[TypeSpec, ...] = (
    TypeSpec(ModelType.ATRI_THINK, "思考模型", GROUP_DIALOG, True),
    # ``atri-chat`` 缺失只降级（intent 直发），不该算「必需」——
    # chat_chain 里的拒绝逻辑只认 atri-think，health 的 required 必须一致。
    TypeSpec(ModelType.ATRI_CHAT, "聊天模型", GROUP_DIALOG, False),
    TypeSpec(ModelType.ATRI_JUDGE, "判断模型", GROUP_FEATURE, False),
    TypeSpec(ModelType.ATRI_MEMORY, "记忆模型", GROUP_FEATURE, False),
    TypeSpec(ModelType.ATRI_PROFILE, "画像模型", GROUP_FEATURE, False),
    TypeSpec(ModelType.ATRI_SCHEDULE, "日程模型", GROUP_FEATURE, False),
    TypeSpec(ModelType.ATRI_OUTFIT, "着装模型", GROUP_FEATURE, False),
    TypeSpec(ModelType.ATRI_VISION, "图片模型", GROUP_FEATURE, False),
    TypeSpec(ModelType.ATRI_EMBEDDING, "向量模型", GROUP_FEATURE, False),
    TypeSpec(ModelType.ATRI_HOME, "家模型", GROUP_OPTIONAL, False),
    TypeSpec(ModelType.ATRI_SUMMARY, "总结模型", GROUP_OPTIONAL, False),
    TypeSpec(ModelType.ATRI_TTS, "语音模型", GROUP_OPTIONAL, False),
)

TYPE_LABELS: dict[str, str] = {spec.type.value: spec.label for spec in TYPE_SPECS}


@dataclass
class TypeHealth:
    type: ModelType
    label: str
    group: str
    required: bool
    registered: list[str]
    preferred: str
    ok: bool
    reason: str

    @property
    def type_key(self) -> str:
        return self.type.value


def check_agent_model_health() -> list[TypeHealth]:
    """检查所有 Agent 类型的注册与指定情况。"""
    health: list[TypeHealth] = []
    for spec in TYPE_SPECS:
        registered = [m.name for m in llm_manager.get_models(spec.type.value)]
        preferred = llm_manager.get_preferred(spec.type.value)
        ok = bool(registered)
        reason = "" if ok else "未注册任何该类型的模型"
        if ok and preferred and preferred not in registered:
            ok = False
            reason = f"指定的模型 '{preferred}' 未注册为该类型"
        health.append(
            TypeHealth(
                type=spec.type,
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


def can_think(health: list[TypeHealth] | None = None) -> bool:
    """思考链路是否可用。聊天入口只用这一个判断决定是否接活。"""
    items = {item.type: item for item in (health or check_agent_model_health())}
    think = items.get(ModelType.ATRI_THINK)
    return bool(think and think.ok)


def can_chat(health: list[TypeHealth] | None = None) -> bool:
    items = {item.type: item for item in (health or check_agent_model_health())}
    chat = items.get(ModelType.ATRI_CHAT)
    return bool(chat and chat.ok)


def log_health(health: list[TypeHealth] | None = None) -> None:
    """把缺失类型写进日志：必需 error，其余 warning。"""
    items = health or check_agent_model_health()
    for item in items:
        if item.ok:
            continue
        message = f"Agent 模型 {item.label}（{item.type_key}）不可用：{item.reason}"
        if item.required:
            log.error(message)
        else:
            log.warning(message)
    missing = [item for item in items if item.required and not item.ok]
    if missing:
        names = "、".join(f"{item.label}({item.type_key})" for item in missing)
        log.error(
            f"Agent 缺少必需模型类型：{names}。"
            "请在 WebUI 的「AI模型设置」中为对应类型选择模型。"
        )


def health_snapshot(health: list[TypeHealth] | None = None) -> dict:
    """给 WebUI / API 的结构化快照。"""
    items = health or check_agent_model_health()
    groups: dict[str, list[dict]] = {key: [] for key in GROUP_LABELS}
    for item in items:
        groups.setdefault(item.group, []).append(
            {
                "type": item.type_key,
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
            item.type_key for item in items if item.required and not item.ok
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
    "TYPE_LABELS",
    "TYPE_SPECS",
    "TypeHealth",
    "can_chat",
    "can_think",
    "check_agent_model_health",
    "health_snapshot",
    "log_health",
    "missing_required",
]
