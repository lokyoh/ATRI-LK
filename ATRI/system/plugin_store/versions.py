from ATRI.version import compare_versions, parse_version


def same_version(left: str, right: str) -> bool:
    """按数值逐段比较版本号，``1.2`` 与 ``1.2.0`` 视为同一版本。

    用字符串全等会让作者把 ``1.2`` 写成 ``1.2.0`` 时被误判成「需更新」，
    而更新后版本号依旧不等，于是永久提示更新。
    """
    left_parts = parse_version(left)
    right_parts = parse_version(right)
    if left_parts is None or right_parts is None:
        return str(left) == str(right)
    return compare_versions(left_parts, right_parts) == 0
