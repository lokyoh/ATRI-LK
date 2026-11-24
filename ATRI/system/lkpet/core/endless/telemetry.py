"""无尽模式的行为数据收集（埋点）。

对齐 ``ATRI/system/agent/agent/telemetry.py`` 的分段与开关：

    enabled          总开关，默认关
    include_content  是否记录正文片段，默认关

**一局一个文件**：``telemetry/<run_id>.jsonl``，run_id 是开局时间戳 +
随机后缀。按时间分文件会把一局拆到两个文件里，跨零点就没法按局分析了。

**每局显式传 run_id**：单例只放配置，不放到「当前局」——多玩家同时在线时
那种隐式状态一定会串。

**绝不抛异常**：埋点失败必须静默，它是旁路观测，不能因为磁盘满或权限
问题把正常战斗搞挂。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from ATRI.log import log

SCHEMA_VERSION = 2
"""事件形状的版本号；改了字段就地升级，旧文件按旧版本读。"""

RETENTION_DAYS = 14
"""按局分文件后文件数会涨，超过这个天数的整份删除。"""

PREVIEW_CHARS = 120

FALLBACK_RUN_ID = "_unbound"
"""没有 run_id 时落到这个文件，而不是悄悄丢掉。"""


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

    def _sweep(self, root: Path) -> None:
        """删掉超过 RETENTION_DAYS 的整份局日志。"""
        cutoff = time.time() - RETENTION_DAYS * 86400
        for path in root.glob("*.jsonl"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue

    def start_run(self, run_id: str) -> None:
        """开一局先把旧文件清一遍，再让后续事件写进这一局的文件。"""
        if not self.enabled or not run_id:
            return
        try:
            self._sweep(_telemetry_dir())
        except Exception as exc:
            log.debug(f"无尽埋点清理失败：{exc}")

    def record(self, run_id: str, event: str, **fields: Any) -> None:
        """写一条事件。任何异常都在这里吞掉。"""
        if not self.enabled:
            return
        try:
            payload: dict[str, Any] = {
                "v": SCHEMA_VERSION,
                "ts": round(time.time(), 3),
                "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "run": str(run_id or FALLBACK_RUN_ID),
                "event": str(event),
            }
            payload.update({k: v for k, v in fields.items() if v is not None})
            line = json.dumps(payload, ensure_ascii=False, default=str)
            target = _telemetry_dir() / f"{payload['run']}.jsonl"
            with (
                self._lock,
                open(target, "a", encoding="utf-8") as handle,
            ):
                handle.write(line + "\n")
            self._fail_streak = 0
        except Exception as exc:
            self._fail_streak += 1
            if self._fail_streak == 1:
                log.warning(f"无尽埋点写入失败，数据将不被记录：{exc}")
            else:
                log.debug(f"无尽埋点写入失败（第 {self._fail_streak} 次）：{exc}")

    # ---- 事件 --------------------------------------------------------

    def run_start(self, run_id: str, *, stage: int, floor: int, seed: int) -> None:
        self.record(run_id, "run_start", stage=stage, floor=floor, seed=seed)

    def run_end(
        self,
        run_id: str,
        *,
        floor: int,
        best: int,
        runs: int,
        reason: str = "retreat",
    ) -> None:
        self.record(run_id, "run_end", floor=floor, best=best, runs=runs, reason=reason)

    def run_abandon(self, run_id: str, *, floor: int) -> None:
        """上一局被「开始」直接覆盖时补一条收尾，否则它没有结局。"""
        self.record(run_id, "run_end", floor=floor, reason="abandon")

    def floor_enter(
        self,
        run_id: str,
        *,
        floor: int,
        stage: int,
        kind: str,
        region: str,
        resumed: bool = False,
    ) -> None:
        """进入一层。``resumed`` 表示「继续」重进，不是新走到这一层。"""
        self.record(
            run_id,
            "floor_enter",
            floor=floor,
            stage=stage,
            kind=kind,
            region=region,
            resumed=resumed or None,
        )

    def choice(
        self,
        run_id: str,
        *,
        floor: int,
        kind: str,
        index: int,
        total: int,
    ) -> None:
        self.record(run_id, "choice", floor=floor, kind=kind, index=index, total=total)

    def battle(
        self,
        run_id: str,
        *,
        floor: int,
        enemy_kind: str,
        floor_kind: str,
        frames: int,
        steps: int,
        victory: bool,
        timed_out: bool,
        hp_before: int,
        acts: dict[str, int] | None = None,
        pet_hp_pct: float,
    ) -> None:
        """一场战斗的结果。

        ``steps`` 是玩家**真正发出**的指令条数（不含「点开始战斗」那一下）。
        ``frames`` 取代原来的 turn：手动模式下每步只推进约 2 帧，
        回合数几乎恒为 1，没有信息量。

        ``acts`` 是本场玩家发了哪几类指令（attack / skill / defend / meditate）。
        没有它就看不出技能是不是死选项、防御有没有人用。
        """
        self.record(
            run_id,
            "battle",
            floor=floor,
            enemy_kind=enemy_kind,
            floor_kind=floor_kind,
            frames=int(frames),
            steps=int(steps),
            victory=victory,
            timed_out=timed_out,
            hp_before=int(hp_before),
            acts=dict(acts or {}) or None,
            pet_hp_pct=round(pet_hp_pct, 3),
        )

    def level_up(
        self, run_id: str, *, floor: int, level: int, key: str, pending: int
    ) -> None:
        self.record(
            run_id, "level_up", floor=floor, level=level, key=key, pending=pending
        )

    def shop_act(
        self,
        run_id: str,
        *,
        floor: int,
        stage: int,
        action: str,
        index: int = 0,
        price: int = 0,
        refreshes: int = 0,
    ) -> None:
        self.record(
            run_id,
            "shop",
            floor=floor,
            stage=stage,
            action=action,
            index=index,
            price=price,
            refreshes=refreshes,
        )

    def relic_gain(
        self,
        run_id: str,
        *,
        floor: int,
        relic_id: str,
        tier: int,
        set_key: str = "",
        source: str = "drop",
        count: int = 1,
    ) -> None:
        """``set_key`` 用来统计套装件数——没有它算不出凑了几件。"""
        self.record(
            run_id,
            "relic_gain",
            floor=floor,
            id=relic_id,
            tier=tier,
            set=set_key or None,
            source=source,
            count=count,
        )

    def gold(
        self,
        run_id: str,
        *,
        floor: int,
        source: str,
        amount: int,
        coins: int,
    ) -> None:
        self.record(
            run_id, "gold", floor=floor, source=source, amount=amount, coins=coins
        )

    def gear_gain(
        self,
        run_id: str,
        *,
        floor: int,
        gear_id: str,
        slot: str,
        tier: int,
        source: str,
        auto_equipped: bool,
    ) -> None:
        self.record(
            run_id,
            "gear_gain",
            floor=floor,
            id=gear_id,
            slot=slot,
            tier=tier,
            source=source,
            auto_equipped=auto_equipped,
        )

    def error(self, run_id: str, *, where: str, message: str) -> None:
        self.record(run_id, "error", where=where, message=str(message)[:200])


telemetry = Telemetry()
"""全局单例；配置改动由 ``configure`` 同步，一局的 run_id 由调用方逐次传入。"""


def configure(enabled: bool, include_content: bool) -> None:
    telemetry.configure(enabled=enabled, include_content=include_content)
