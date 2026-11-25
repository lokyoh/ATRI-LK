from pathlib import Path

import yaml


def validate_plugin_files(root: Path, is_dir: bool) -> list[str]:
    """安装前静态检查插件包，返回问题列表（空表示通过）。

    只做不需要执行插件代码的检查：文件在不在、能不能编译、``meta.yml``
    能不能解析。给 WebUI 的「强制安装」提供真实依据，而不是只弹个确认框。
    """
    problems: list[str] = []
    if not root.exists():
        return [f"插件文件不存在：{root}"]

    if not is_dir:
        entry_file = root if root.suffix == ".py" else root.with_name(f"{root.name}.py")
        if not entry_file.is_file():
            return [f"插件入口文件不存在：{entry_file}"]
    else:
        entry_file = root / "__init__.py"
        if not entry_file.is_file():
            problems.append(f"目录内缺少 __init__.py：{root}")

    for py_file in [entry_file] if entry_file.is_file() else []:
        try:
            compile(py_file.read_text(encoding="utf-8"), str(py_file), "exec")
        except SyntaxError as e:
            problems.append(f"{py_file.name} 语法错误：第 {e.lineno} 行 {e.msg}")
        except OSError as e:
            problems.append(f"{py_file.name} 读取失败：{e}")

    # 单文件型插件没有自己的目录，也就没有 meta.yml 可查
    meta_path = root / "meta.yml"
    if is_dir and meta_path.is_file():
        try:
            meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            problems.append(f"meta.yml 解析失败：{e}")
        else:
            if not isinstance(meta, dict):
                problems.append("meta.yml 顶层应为映射")
            elif not str(meta.get("version") or "").strip():
                problems.append("meta.yml 缺少 version")

    return problems
