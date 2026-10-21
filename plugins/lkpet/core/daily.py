from datetime import date, datetime
from typing import Any

from ATRI.utils.datetime import now as now_td
from ATRI.utils.datetime import today as today_date

from ..utils.callbacks import Callback, run_callback
from .status import apply_daily_decay, update_emotion_state, update_health_state


def get_current_pet_day(cycle_started_at: datetime, today: date | None = None) -> int:
    """Return elapsed local calendar days in the pet's current cycle."""
    current_date = today or today_date()
    return max(0, (current_date - cycle_started_at.date()).days)


def _load_daily_config() -> dict[str, Any]:
    from ..config_loader import load_config

    return load_config("daily")


def _daily_log(pet: Any, day: int, changes: dict[str, int]) -> str:
    parts = [f"第 {day} 天"]
    for key, label in (
        ("hunger", "饥饿"),
        ("mood", "心情"),
        ("cleanliness", "清洁"),
        ("fatigue", "疲劳"),
        ("affection", "亲密"),
    ):
        change = changes[key]
        if change:
            parts.append(f"{label} {change:+d}")
    if pet.health_state != "healthy":
        parts.append(f"状态：{pet.health_state}")
    return "，".join(parts)


async def settle_days(
    pet: Any,
    from_day: int,
    to_day: int,
    *,
    config: dict[str, Any] | None = None,
    daily_log_writer: Callback | None = None,
    reincarnation_checker: Callback | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Settle every elapsed personal day; reincarnation is reported, never automatic."""
    cfg = config if config is not None else _load_daily_config()
    elapsed_days = max(0, to_day - from_day)
    if elapsed_days == 0:
        return {"days_settled": 0, "report": [], "reincarnation": None}

    max_report_days = max(0, int(cfg["max_offline_days"]))
    report: list[dict[str, Any]] = []
    first_report_day = max(from_day + 1, to_day - max_report_days + 1)

    for day in range(from_day + 1, to_day + 1):
        pet.age_days += 1
        changes = apply_daily_decay(pet, cfg)
        pet.action_points = cfg["action_points"]["max_per_day"]
        pet.ap_reset_day = day
        changes["age_days"] = 1
        changes["action_points"] = pet.action_points

        pet.daily_flags = dict.fromkeys(cfg["daily_flags"], False)
        update_health_state(pet)
        update_emotion_state(pet)
        log_text = _daily_log(pet, day, changes)
        report.append({"day": day, "changes": changes, "log": log_text})

        if len(report) > max_report_days:
            report.pop(0)
        if day >= first_report_day:
            await run_callback(daily_log_writer, pet, log_text)

    pet.current_day = to_day
    pet.last_settlement_day = to_day
    pet.last_settlement_at = now or now_td()

    reincarnation = await run_callback(reincarnation_checker, pet)
    return {
        "days_settled": elapsed_days,
        "report": report,
        "report_truncated": elapsed_days > max_report_days,
        "action_points": pet.action_points,
        "reincarnation": reincarnation,
    }


async def settle_one_day(
    pet: Any,
    day: int,
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Settle one personal day without automatically ending the pet's cycle."""
    cfg = config if config is not None else _load_daily_config()
    pet.age_days += 1
    changes = apply_daily_decay(pet, cfg)
    pet.action_points = cfg["action_points"]["max_per_day"]
    pet.ap_reset_day = day
    changes["age_days"] = 1
    changes["action_points"] = pet.action_points
    pet.daily_flags = dict.fromkeys(cfg["daily_flags"], False)
    update_health_state(pet)
    update_emotion_state(pet)
    return {"day": day, "changes": changes, "log": _daily_log(pet, day, changes)}


async def check_daily_reset(
    pet: Any,
    *,
    config: dict[str, Any] | None = None,
    daily_log_writer: Callback | None = None,
    reincarnation_checker: Callback | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_day = get_current_pet_day(
        pet.cycle_started_at,
        (now or now_td()).date(),
    )
    if pet.last_settlement_day >= current_day:
        return {"need_settle": False}

    result = await settle_days(
        pet,
        pet.last_settlement_day,
        current_day,
        config=config,
        daily_log_writer=daily_log_writer,
        reincarnation_checker=reincarnation_checker,
        now=now,
    )
    return {"need_settle": True, **result}


def get_daily_summary(pet: Any) -> dict[str, Any]:
    return {
        "age_days": pet.age_days,
        "lifespan_days": pet.lifespan_days,
        "action_points": pet.action_points,
        "daily_flags": pet.daily_flags,
        "health_state": pet.health_state,
        "emotion_state": pet.emotion_state,
    }
