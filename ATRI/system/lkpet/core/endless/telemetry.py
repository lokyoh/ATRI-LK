"""无尽模式的行为数据收集（埋点）。

对齐 ``ATRI/system/agent/agent/telemetry.py`` 的分段与开关：

    enabled          总开关，默认关
    include_content  是否记录正文片段，默认关

**绝不抛异常**：埋点失败必须静默——它是旁路观测，不能因为磁盘满或权限
问题把正常战斗搞挂。

事件全部是**数值指标**，不记玩家号与正文；正文只在 include_content 打开时
按 120 字截断记一小段，用来核对「玩家看到的和引擎理解的是不是一回事」。
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ATRI.log import log

MAX_FILE_BYTES = 1 * 1024 * 1024
"""单个数据文件上限：1MB ≈ 数千条事件，够分析一周而不至于无感膨胀。"""

RETENTION_DAYS = 14

PREVIEW_CHARS = 120


def _telemetry_dir() -> Path:
    from ...utils.paths import RUNTIME_DATA_ROOT

    path = RUNTIME_DATA_ROOT / "telemetry"
    path.mkdir(parents=True, exist_ok=True)
    return path


class Telemetry:
    """无尽模式的采集器。默认全关。"""

    def __init__(self) -> None:
        self.enabled = False
        self.include_content = False
        self._lock = threading.Lock()
        self._fail_streak = 0

    def configure(self, enabled: bool, include_content: bool) -> None:
        self.enabled = bool(enabled)
        self.include_content = bool(include_content)

    def preview(self, text: Any) -> str | None:
        """按配置返回正文预览；未开启时返回 None。"""
        if not self.include_content or not text:
            return None
        body = str(text)
        return body[:PREVIEW_CHARS] + ("…" if len(body) > PREVIEW_CHARS else "")

    def _current_file(self, root: Path) -> Path:
        day = datetime.now().strftime("%Y-%m-%d")
        candidate = root / f"{day}.jsonl"
        if candidate.exists() and candidate.stat().st_size >= MAX_FILE_BYTES:
            index = 1
            while True:
                rolled = root / f"{day}.{index}.jsonl"
                if not rolled.exists():
                    return rolled
                if rolled.stat().st_size < MAX_FILE_BYTES:
                    return rolled
                index += 1
        return candidate

    def record(self, event: str, **fields: Any) -> None:
        """写一条事件。任何异常都在这里吞掉。"""
        if not self.enabled:
            return
        try:
            payload: dict[str, Any] = {
                "ts": round(time.time(), 3),
                "event": str(event),
            }
            payload.update({k: v for k, v in fields.items() if v is not None})
            line = json.dumps(payload, ensure_ascii=False, default=str)
            root = _telemetry_dir()
            with self._lock:
                target = self._current_file(root)
                with open(target, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            self._fail_streak = 0
        except Exception as exc:
            self._fail_streak += 1
            if self._fail_streak == 1:
                log.warning(f"无尽埋点写入失败，数据将不被记录：{exc}")
            else:
                log.debug(f"无尽埋点写入失败（第 {self._fail_streak} 次）：{exc}")

    # ---- 无尽模式专用埋点 ----------------------------------------------

    def run_start(self, *, stage: int, floor: int, seed: int) -> None:
        """开一局。"""
        self.record("run_start", stage=stage, floor=floor, seed=seed)

    def run_end(
        self, *, floor: int, best: int, runs: int, reason: str = "retreat"
    ) -> None:
        """一局结束：只记层数与结局，不记数值产出。"""
        self.record("run_end", floor=floor, best=best, runs=runs, reason=reason)

    def floor_enter(self, *, floor: int, stage: int, kind: str, region: str) -> None:
        """进入一层。"""
        self.record("floor_enter", floor=floor, stage=stage, kind=kind, region=region)

    def choice(self, *, floor: int, kind: str, index: int, total: int) -> None:
        """玩家选了哪个选项。``index`` 从 1 起。"""
        self.record("choice", floor=floor, kind=kind, index=index, total=total)

    def battle(
        self,
        *,
        floor: int,
        kind: str,
        turns: int,
        steps: int,
        victory: bool,
        pet_hp_pct: float,
        enemy_alive: int,
    ) -> None:
        """一场战斗的结果。

        ``steps`` 是玩家发的指令条数——这是评估「一场战斗有多累」的核心指标，
        比回合数更贴近体感。
        """
        self.record(
            "battle",
            floor=floor,
            kind=kind,
            turns=turns,
            steps=steps,
            victory=victory,
            pet_hp_pct=round(pet_hp_pct, 3),
            enemy_alive=enemy_alive,
        )

    def level_up(self, *, level: int, key: str, pending: int) -> None:
        """升级 4 选 1 的选择。"""
        self.record("level_up", level=level, key=key, pending=pending)

    def shop_act(
        self,
        *,
        floor: int,
        stage: int,
        action: str,
        index: int = 0,
        price: int = 0,
        refreshes: int = 0,
    ) -> None:
        """商店动作：``open`` / ``buy`` / ``refresh`` / ``leave``。"""
        self.record(
            "shop",
            floor=floor,
            stage=stage,
            action=action,
            index=index,
            price=price,
            refreshes=refreshes,
        )

    def relic_gain(self, *, floor: int, tier: int, source: str, count: int = 1) -> None:
        """遗物获得：只记档位与来源，不记具体 id。"""
        self.record("relic_gain", floor=floor, tier=tier, source=source, count=count)

    def error(self, *, where: str, message: str) -> None:
        """埋点自己的失败也留一条，方便排查采集链。"""
        self.record("error", where=where, message=str(message)[:200])


telemetry = Telemetry()
"""全局单例；配置改动由 ``configure`` 同步。"""


def configure(enabled: bool, include_content: bool) -> None:
    telemetry.configure(enabled=enabled, include_content=include_content)
