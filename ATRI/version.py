import re

COMPAT_FRAMEWORK = ("0.24", "0.25")


def framework_version() -> str:
    from ATRI import __version__

    match = re.search(r"(\d+)[-_.](\d+)$", str(__version__))
    if not match:
        return ""
    return f"{int(match.group(1))}.{int(match.group(2))}"


def parse_version(text) -> tuple[int, ...] | None:
    if not text:
        return None
    match = re.match(r"^[vV]?(\d+(?:\.\d+)*)", str(text).strip())
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def compare_versions(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    width = max(len(left), len(right))
    left = left + (0,) * (width - len(left))
    right = right + (0,) * (width - len(right))
    return (left > right) - (left < right)


def is_supported(framework: str) -> bool:
    return framework_status(framework) == "supported"


def framework_status(framework: str) -> str:
    current = parse_version(framework)
    if current is None:
        return "unknown"
    low, high = COMPAT_FRAMEWORK
    if compare_versions(current, parse_version(low)) < 0:
        return "outdated"
    if compare_versions(current, parse_version(high)) > 0:
        return "newer"
    return "supported"


def compat_range() -> str:
    return f"{COMPAT_FRAMEWORK[0]}-{COMPAT_FRAMEWORK[1]}"


def lkapi_version() -> int | None:
    try:
        from ATRI.system import lkapi
    except Exception:
        return None
    value = getattr(lkapi, "API_VERSION", None)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None
