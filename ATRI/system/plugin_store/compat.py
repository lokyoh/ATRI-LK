import re
from collections.abc import Mapping
from dataclasses import dataclass

from ATRI.service import Service
from ATRI.version import (
    compat_range,
    framework_status,
    framework_version,
    lkapi_version,
)

_SINGLE_VERSION_RE = re.compile(r"^[vV]?(\d+(?:\.\d+)*)$")


def normalize_type(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for member in Service.ServiceType:
        if member == Service.ServiceType.CLOSED:
            continue
        if text in (member.value, member.name, member.key):
            return member.value
    return text


def known_types() -> list[str]:
    return [
        member.value
        for member in Service.ServiceType
        if member != Service.ServiceType.CLOSED
    ]


def _coerce_version(value) -> str:
    if value is None or isinstance(value, bool):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    match = _SINGLE_VERSION_RE.match(text)
    if not match:
        return ""
    parts = tuple(int(part) for part in match.group(1).split("."))
    return ".".join(str(part) for part in parts)


def _coerce_int(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if number != int(number):
        return None
    return int(number)


@dataclass(frozen=True)
class PluginRequirement:
    type: str = ""
    """插件种类，ServiceType.value；空串表示未声明"""
    framework: str = ""
    """插件开发所依据的框架版本，单一版本号；空串表示未声明"""
    lkapi: int | None = None
    """插件所依赖的 lkapi 接口版本；None 表示未声明"""

    @classmethod
    def from_mapping(cls, data: Mapping | None) -> "PluginRequirement":
        data = data or {}
        return cls(
            type=normalize_type(data.get("type") or data.get("plugin_type")),
            framework=_coerce_version(data.get("framework")),
            lkapi=_coerce_int(data.get("lkapi")),
        )

    @property
    def declared(self) -> bool:
        return bool(self.type or self.framework or self.lkapi is not None)


@dataclass(frozen=True)
class CompatResult:
    ok: bool
    """是否兼容"""
    reason: str
    """不兼容原因；兼容时为空串"""
    framework: str
    """当前框架版本"""
    lkapi: int | None
    """当前 lkapi 版本"""
    supported: str
    """当前框架的兼容区间"""
    status: str = "unknown"
    """兼容状态：supported / unknown / outdated / newer"""

    @property
    def label(self) -> str:
        return STATUS_LABELS.get(self.status, self.reason or "未知兼容性")


STATUS_LABELS = {
    "supported": "兼容",
    "unknown": "未知兼容性",
    "outdated": "版本过时，可能不兼容",
    "newer": "版本过新，可能不兼容",
}


def check(requirement: PluginRequirement) -> CompatResult:
    problems: list[str] = []
    current_framework = framework_version()
    current_lkapi = lkapi_version()
    supported = compat_range()
    status = "supported"

    if not requirement.framework:
        status = "unknown"
    else:
        framework_kind = framework_status(requirement.framework)
        if framework_kind == "outdated":
            status = "outdated"
            problems.append(
                f"插件基于框架 {requirement.framework}，"
                f"低于兼容范围 {supported}（当前 {current_framework}）"
            )
        elif framework_kind == "newer":
            status = "newer"
            problems.append(
                f"插件基于框架 {requirement.framework}，"
                f"高于兼容范围 {supported}（当前 {current_framework}）"
            )

    if requirement.lkapi is not None:
        if current_lkapi is None:
            problems.append(f"无法确定 lkapi 版本（需 {requirement.lkapi}）")
        elif current_lkapi < requirement.lkapi:
            problems.append(f"需 lkapi {requirement.lkapi}（当前 {current_lkapi}）")

    return CompatResult(
        ok=not problems,
        reason="；".join(problems),
        framework=current_framework,
        lkapi=current_lkapi,
        supported=supported,
        status=status,
    )


def lkapi_note(requirement: PluginRequirement) -> str:
    current_lkapi = lkapi_version()
    if requirement.lkapi is None or current_lkapi is None:
        return ""
    if current_lkapi == requirement.lkapi:
        return f"lkapi {current_lkapi} 存在变动说明，使用前请确认已适配"
    return (
        f"插件基于 lkapi {requirement.lkapi}，当前 lkapi {current_lkapi} 存在变动说明"
    )
