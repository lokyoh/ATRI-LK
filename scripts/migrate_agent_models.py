"""把 provider.yml 里的旧模型类型迁移到 Agent 专用 ``atri-*``。

背景：Agent 不再兼容旧类型。``atri`` 已删除；``chat`` / ``tool`` /
``image`` / ``embedding`` / ``tts`` 仍供非 Agent 服务使用，但 Agent 不会
再读它们。本脚本只**追加** atri 类型，不删除通用类型，避免影响
``lkpet`` / ``trpg`` / ``feed``。

默认只打印计划；确认后加 ``--apply`` 写入（写入前自动备份）。

用法::

    python scripts/migrate_agent_models.py
    python scripts/migrate_agent_models.py --apply
"""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path

import yaml

PROVIDER_PATH = Path("data/config/system/provider.yml")

#: 旧类型 → 需要追加的 Agent 类型
TYPE_MAP: dict[str, tuple[str, ...]] = {
    "atri": ("atri-think",),
    "chat": ("atri-chat",),
    "tool": (
        "atri-judge",
        "atri-memory",
        "atri-profile",
        "atri-schedule",
        "atri-summary",
    ),
    "image": ("atri-vision",),
    "embedding": ("atri-embedding",),
    "tts": ("atri-tts",),
}


def _as_types(value) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return []


def plan_changes(data: dict) -> list[tuple[str, list[str], list[str]]]:
    """返回 [(模型名, 原类型, 新类型)]；不修改传入数据。"""
    changes: list[tuple[str, list[str], list[str]]] = []
    for provider in data.values():
        if not isinstance(provider, dict):
            continue
        models = provider.get("models") or []
        if not isinstance(models, list):
            continue
        for model in models:
            if not isinstance(model, dict):
                continue
            original = _as_types(model.get("type"))
            updated = list(original)
            for old in original:
                for new in TYPE_MAP.get(old, ()):
                    if new not in updated:
                        updated.append(new)
            # ``atri`` 已删除，保留它只会在启动时刷迁移告警。
            updated = [item for item in updated if item != "atri"]
            if updated != original:
                changes.append((str(model.get("name", "")), original, updated))
    return changes


def apply_changes(data: dict) -> None:
    for provider in data.values():
        if not isinstance(provider, dict):
            continue
        for model in provider.get("models") or []:
            if not isinstance(model, dict):
                continue
            original = _as_types(model.get("type"))
            updated = list(original)
            for old in original:
                for new in TYPE_MAP.get(old, ()):
                    if new not in updated:
                        updated.append(new)
            updated = [item for item in updated if item != "atri"]
            if updated != original:
                model["type"] = updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="写入 provider.yml")
    parser.add_argument(
        "--path",
        type=Path,
        default=PROVIDER_PATH,
        help=f"provider.yml 路径（默认 {PROVIDER_PATH}）",
    )
    args = parser.parse_args()

    if not args.path.exists():
        print(f"找不到 {args.path}，无需迁移")
        return 1

    data = yaml.safe_load(args.path.read_bytes()) or {}
    if not isinstance(data, dict):
        print(f"{args.path} 顶层不是对象，已中止")
        return 1

    changes = plan_changes(data)
    if not changes:
        print("没有需要迁移的模型类型")
        return 0

    for name, old, new in changes:
        print(f"- {name or '(未命名)'}: {old} -> {new}")

    if not args.apply:
        print(f"\n共 {len(changes)} 个模型待迁移；确认后加 --apply 写入")
        return 0

    backup = args.path.with_name(f"{args.path.name}.bak.{datetime.now():%Y%m%d_%H%M%S}")
    shutil.copy2(args.path, backup)
    apply_changes(data)
    args.path.write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    print(f"\n已写入 {args.path}；备份：{backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
