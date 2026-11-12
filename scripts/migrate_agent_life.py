"""旧 `ATRI_schedule.json` 迁移到生活系统的按日结构。

- 旧文件：`<sys_config>/ATRI_schedule.json`，结构 `{date: {outfit, schedule: [...]}}`。
- 新结构：`data/plugins/agent/life/schedule/<date>.json`，条目标记 `legacy`。
- 穿搭：旧 `outfit` 是单套，白天与晚上都填同一段，并注明来源。
- 幂等：目标日期已有填入条目时跳过，不覆盖新数据。
- 迁移完成写 `migration-report.json`，列出转换天数与 legacy 条数。

用法::

    python scripts/migrate_agent_life.py
    python scripts/migrate_agent_life.py --file path/to/ATRI_schedule.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: 仓库根；`scripts/` 是 sys.path[0]，必须手动补进去，否则 `import ATRI` 会失败。
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import nonebot  # noqa: E402

try:  # NoneBot 尚未初始化：ATRI.event 的装饰器需要 driver 存在
    nonebot.get_driver()
except Exception:
    nonebot.init(driver="~fastapi+~websockets+~httpx")

from ATRI.dir import SYS_CONFIG_DIR  # noqa: E402
from ATRI.log import log  # noqa: E402
from ATRI.system.agent.agent.life import (  # noqa: E402
    DayPlan,
    EntryKind,
    LifeSystem,
    OutfitPlan,
    ScheduleEntry,
    parse_time,
)
from ATRI.system.agent.agent.life.store import read_json, write_json  # noqa: E402

LEGACY_FILE = SYS_CONFIG_DIR / "ATRI_schedule.json"
REPORT_FILE = "migration-report.json"


def convert_entries(payload: dict) -> list[ScheduleEntry]:
    entries: list[ScheduleEntry] = []
    for item in payload.get("schedule") or []:
        if not isinstance(item, dict):
            continue
        try:
            start = parse_time(item.get("start_time"))
            end = parse_time(item.get("end_time"))
        except (TypeError, ValueError) as exc:
            log.warning(f"迁移旧日程条目失败，已跳过：{item}（{exc}）")
            continue
        entries.append(
            ScheduleEntry(
                start=start,
                end=end,
                kind=EntryKind.STAY,
                activity=str(item.get("schedule") or "").strip(),
                place="",
                source="legacy",
                note="旧日程未记录地点",
            )
        )
    return entries


def migrate_one(date: str, payload: dict, life: LifeSystem) -> int:
    """返回写入的 legacy 条数；已有填入条目时返回 0。"""
    target = life.schedules.path_for(date)
    existing = read_json(target)
    if existing.get("entries"):
        log.info(f"{date} 已有填入日程，跳过")
        return 0
    entries = convert_entries(payload)
    if not entries:
        return 0
    plan = DayPlan(date=date, entries=entries)
    write_json(target, plan.to_dict())

    outfit_path = life.outfits.path_for(date)
    if not read_json(outfit_path).get("day"):
        text = str(payload.get("outfit") or "").strip()
        if text:
            write_json(
                outfit_path,
                OutfitPlan(
                    date=date, day=text, night="（旧版未记录晚上穿搭）"
                ).to_dict(),
            )
    return len(entries)


def write_report(life: LifeSystem, days: int, entries: int, source: Path) -> None:
    report = {
        "generated_at": __import__("time").time(),
        "days": days,
        "legacy_entries": entries,
        "source": str(source),
    }
    write_json(life.root / REPORT_FILE, report)


def backup_legacy(path) -> None:
    """迁移前备份旧文件；已有同名备份时跳过，避免重复写。"""
    import shutil
    from datetime import datetime

    if not path.is_file():
        return
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = path.with_suffix(f"{path.suffix}.bak.{stamp}")
    try:
        shutil.copy2(path, target)
        log.info(f"旧日程已备份：{target}")
    except OSError as exc:
        log.warning(f"旧日程备份失败（继续迁移）：{exc}")


def migrate(path=LEGACY_FILE) -> int:
    life = LifeSystem()
    life.ensure()
    if not path.is_file():
        log.info(f"没有旧日程文件 {path}，无需迁移")
        return 0
    backup_legacy(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        log.warning(f"旧日程文件读取失败：{exc}")
        return 0
    if not isinstance(data, dict):
        return 0
    days = 0
    total = 0
    for date, payload in data.items():
        if not isinstance(payload, dict):
            continue
        count = migrate_one(str(date), payload, life)
        if count:
            days += 1
            total += count
    write_report(life, days, total, path)
    log.info(f"旧日程迁移完成：{days} 天 / {total} 条 legacy")
    return days


def main() -> int:
    parser = argparse.ArgumentParser(description="迁移旧 ATRI_schedule.json")
    parser.add_argument("--file", default=str(LEGACY_FILE), help="旧日程文件路径")
    args = parser.parse_args()
    migrate(Path(args.file))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
