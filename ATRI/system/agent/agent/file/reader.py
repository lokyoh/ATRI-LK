import re
from pathlib import Path

# ========== 黑名单配置 ==========

# 1. 敏感目录名（任意层级出现即拒绝）
BLACKLIST_DIRS = {
    ".git",
    ".svn",
    ".hg",  # 版本控制
    ".ssh",
    ".gnupg",  # 密钥
    "__pycache__",
    ".pytest_cache",  # 缓存
    "node_modules",
    ".venv",
    "venv",  # 依赖
    ".idea",
    ".vscode",  # IDE
}

# 2. 敏感文件名（精确匹配）
BLACKLIST_FILES = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
    "id_dsa",
    "credentials",
    "credentials.json",
    "secrets.json",
    "secrets.yaml",
    ".netrc",
    ".htpasswd",
    "shadow",
    "passwd",
    "agent_config.json",
    "provider.yml",
}

# 3. 敏感扩展名（后缀匹配）
BLACKLIST_EXTS = {
    ".pem",
    ".key",
    ".crt",
    ".pfx",
    ".p12",  # 证书/私钥
    ".keystore",
    ".jks",
    ".sqlite",
    ".db",  # 数据库
}

# 4. 敏感路径正则（更灵活，匹配完整路径）
BLACKLIST_PATTERNS = [
    re.compile(r"/\.git/"),
    re.compile(r"/\.ssh/"),
    re.compile(r"/etc/(passwd|shadow|sudoers)"),
    re.compile(r".*secret.*", re.IGNORECASE),
    re.compile(r".*password.*", re.IGNORECASE),
    re.compile(r".*token.*", re.IGNORECASE),
]

# 5. 允许访问的根目录（可选，防止路径穿越到系统目录）
#    留空表示不限制根目录，只靠黑名单
ALLOWED_ROOTS: list[Path] = [Path.cwd()]


def is_path_safe(file_path: Path) -> tuple[bool, str]:
    """
    黑名单安全检查。

    Returns:
        (是否安全, 拒绝原因)
    """
    # ---------- 1. 解析为绝对路径，消除 .. 和符号链接 ----------
    try:
        resolved = file_path.resolve(strict=False)
    except Exception as e:
        return False, f"路径解析失败：{e}"

    parts = resolved.parts  # 路径各段，如 ('/', 'home', 'user', '.ssh', 'id_rsa')

    # ---------- 2. 目录名黑名单：任意层级命中即拒绝 ----------
    for part in parts:
        if part in BLACKLIST_DIRS:
            return False, f"命中敏感目录：{part}"

    # ---------- 3. 文件名黑名单（精确匹配） ----------
    name = resolved.name
    if name in BLACKLIST_FILES:
        return False, f"命中敏感文件：{name}"

    # ---------- 4. 扩展名黑名单 ----------
    if resolved.suffix.lower() in BLACKLIST_EXTS:
        return False, f"命中敏感扩展名：{resolved.suffix}"

    # ---------- 5. 正则模式匹配完整路径 ----------
    path_str = str(resolved)
    for pattern in BLACKLIST_PATTERNS:
        if pattern.search(path_str):
            return False, f"命中敏感路径模式：{pattern.pattern}"

    # ---------- 6. 可选：根目录限制 ----------
    if ALLOWED_ROOTS and not any(
        resolved == root or root in resolved.parents for root in ALLOWED_ROOTS
    ):
        return False, f"路径不在允许的根目录内：{resolved}"

    return True, ""


def read_file(path: str, start: int = 1, end: int = 0) -> str:
    # ---- 参数容错 ----
    try:
        start = int(start) if start is not None else 1
    except (TypeError, ValueError):
        start = 1
    try:
        end = int(end) if end is not None else 0
    except (TypeError, ValueError):
        end = 0

    start = max(start, 1)
    end = max(end, 0)

    # ---- 路径解析 ----
    try:
        file_path = Path(path).expanduser()
        # 相对路径基于当前工作目录
        if not file_path.is_absolute():
            file_path = Path.cwd() / file_path
        file_path = file_path.resolve(strict=False)
    except Exception as e:
        return f"错误：路径无效 -> {path} ({e})"

    # ---- 安全检查（黑名单） ----
    safe, reason = is_path_safe(file_path)
    if not safe:
        return f"拒绝访问：{reason} -> {file_path}"

    # ---- 存在性检查 ----
    if not file_path.exists():
        return f"错误：文件不存在 -> {file_path}"
    if not file_path.is_file():
        return f"错误：不是文件 -> {file_path}"

    # ---- 大小限制 ----
    MAX_SIZE = 1024 * 1024  # 1MB
    try:
        size = file_path.stat().st_size
        if size > MAX_SIZE:
            return "错误：文件过大"
    except OSError as e:
        return f"错误：无法获取文件信息 -> {e}"

    # ---- 读取 ----
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        return f"读取失败：{e}"

    total = len(lines)
    end = total if end == 0 else min(end, total)

    if start > total:
        return f"错误：起始行 {start} 超出文件总行数 {total}"
    if start > end:
        return f"错误：起始行 {start} 大于结束行 {end}"

    selected = lines[start - 1 : end]
    body = "".join(f"{i:>4} | {line}" for i, line in enumerate(selected, start=start))

    header = f"[文件: {file_path} | 共 {total} 行 | 显示 {start}-{end}]\n"
    return header + body
