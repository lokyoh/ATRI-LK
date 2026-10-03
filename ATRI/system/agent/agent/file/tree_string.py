from datetime import timedelta
from pathlib import Path

from ATRI.utils.datetime import fromtimestamp, now

default_ignore_dirs = {
    ".git",
    "__pycache__",
    ".idea",
    ".vscode",
    "venv",
    "tests",
    "res",
    "logs",
    "webui",
    "workflows",
    "temp",
    "sql",
    ".pytest_cache",
    ".venv",
}
default_ignore_files = {
    "provider.yml",
    "agent_config.json",
    "config.yml",
    "config_backup.yml",
}

# 需要统计行数的后缀
COUNT_LINES_EXTS = {
    ".py",
    ".json",
    ".md",
    ".txt",
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".cfg",
    ".csv",
}


def count_lines(path: Path) -> int:
    """统计文本文件行数，失败返回 -1"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except Exception:
        return -1


def get_tree_string(
    root_dir,
    prefix="",
    ignore_dirs=None,
    ignore_files=None,
    recent_days=0,
    show_lines=True,
):
    """
    生成目录树字符串。

    Args:
        root_dir:     根目录
        prefix:       递归前缀（内部使用）
        ignore_dirs:  忽略的目录集合
        ignore_files: 忽略的文件集合
        recent_days:  仅显示最近 N 天修改的文件；0 表示不过滤。
                      非 0 时在文件后附加修改日期。
        show_lines:   是否显示文本文件的行数
    """
    if ignore_dirs is None:
        ignore_dirs = default_ignore_dirs
    if ignore_files is None:
        ignore_files = default_ignore_files

    root = Path(root_dir)
    result = []

    try:
        entries = sorted(root.iterdir(), key=lambda x: (x.is_file(), x.name))
    except PermissionError:
        return ""

    # ---- 时间阈值 ----
    cutoff = now() - timedelta(days=recent_days) if recent_days > 0 else None

    filtered = []
    for e in entries:
        if e.name in ignore_dirs or e.name in ignore_files:
            continue

        # 只对文件做时间过滤；目录始终保留（否则无法进入子目录）
        if cutoff is not None and e.is_file():
            try:
                mtime = fromtimestamp(e.stat().st_mtime)
            except OSError:
                continue
            if mtime < cutoff:
                continue

        filtered.append(e)

    entries = filtered

    for i, entry in enumerate(entries):
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "

        if entry.is_dir():
            result.append(f"{prefix}{connector}{entry.name}/")
            extension = "    " if is_last else "│   "
            subtree = get_tree_string(
                entry,
                prefix + extension,
                ignore_dirs,
                ignore_files,
                recent_days,
                show_lines,
            )
            if subtree:
                result.append(subtree.rstrip())
        else:
            # ---- 文件行 ----
            suffix = ""

            # 日期后缀：仅当 recent_days != 0 时显示
            if recent_days > 0:
                try:
                    mtime = fromtimestamp(entry.stat().st_mtime)
                    suffix += f"  [{mtime.strftime('%Y-%m-%d')}]"
                except OSError:
                    pass

            # 行数后缀：仅对指定后缀统计
            if show_lines and entry.suffix.lower() in COUNT_LINES_EXTS:
                n = count_lines(entry)
                if n >= 0:
                    suffix += f"  ({n} 行)"

            result.append(f"{prefix}{connector}{entry.name}{suffix}")

    return "\n".join(result)
