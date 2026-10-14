"""Scheduled daily-settlement notification (task §3.2 step 7「新的一天」通知).

Wired to the plugin's cron job via ``plugin.scheduler_jobs()``. On every run it
walks every pet, settles the days each one has missed (exactly the same
``check_daily_reset`` flow the ``/宠物.状态`` handler uses, so a player who opens
the card first never gets a double settlement), then pushes a「新的一天」card to
the groups listed in ``notify_groups``.

The settlement is authoritative here: the schedule is what advances pets whose
owners never come back, so offline pets keep aging and decaying rather than
freezing at their last login.
"""

from typing import Any

from nonebot import get_bots
from nonebot.adapters.onebot.v11.bot import Bot

from ATRI.log import log

from .. import config as lkpet_config
from ..database import load_all_pets, save_model, pet_table, PET_KEY_COLUMN
from ..utils.texts import text
from . import biography, daily, reincarnation

MAX_MESSAGE_LENGTH = 3500
"""Push cards in batches below this length; a single over-long message fails the send."""


async def _settle_pet(pet: Any) -> dict[str, Any] | None:
    """Advance one pet to its current personal day; ``None`` when nothing was due.

    Biography and reincarnation are wired in exactly as in the command handler,
    so scheduled settlements write daily logs and surface reincarnation hints
    the same way a player-triggered one does.
    """
    result = await daily.check_daily_reset(
        pet,
        daily_log_writer=biography.add_daily_log,
        reincarnation_checker=reincarnation.check_reincarnation_available,
    )
    if not result.get("need_settle"):
        return None
    save_model(pet_table, pet, PET_KEY_COLUMN)
    return result


def _build_message(pet: Any, settle: dict[str, Any]) -> str:
    """Render one pet's「新的一天」card from the externalised texts."""
    max_ap = pet.action_points
    lines = [
        text(
            "daily.new_day",
            "🌅 新的一天到了！",
            name=pet.name,
            action_points=pet.action_points,
            max_action_points=max_ap,
        ),
        text(
            "daily.title",
            "📅 已结算 {days_settled} 天",
            days_settled=settle.get("days_settled", 0),
            to_day=pet.current_day,
        ),
    ]

    reincarnation_check = settle.get("reincarnation")
    if reincarnation_check and reincarnation_check.get("can_reincarnate"):
        lines.append(
            text(
                "daily.reincarnation",
                "",
                reasons="、".join(reincarnation_check.get("reasons", [])),
                points=reincarnation_check.get("estimated_points", 0),
            )
        )
    return "\n".join(line for line in lines if line)


def _notify_groups() -> list[int]:
    """Group ids to push to; empty list means「不主动推送」per the config."""
    if not lkpet_config.get("settle_notify"):
        return []
    return [int(g) for g in lkpet_config.get("notify_groups", [])]


async def settle_and_notify() -> None:
    """The scheduled job: settle every pet, then notify each configured group.

    Never raises: a failure on one pet or one group is logged and skipped so the
    rest of the flock still reaches its new day.
    """
    groups = _notify_groups()
    if not lkpet_config.get("enable"):
        return

    settled: list[tuple[Any, dict[str, Any]]] = []
    for pet in load_all_pets():
        try:
            result = await _settle_pet(pet)
        except Exception as exc:  # one bad row must not stall the whole pass
            log.warning(f"lkpet 日结算跳过 {pet.player_id}: {exc}")
            continue
        if result is not None:
            settled.append((pet, result))

    if not groups or not settled:
        return

    # 每只宠物一张卡；卡片很多时按长度分批，避免超过单条消息上限导致整次推送失败。
    cards = [_build_message(pet, settle) for pet, settle in settled]
    batches: list[str] = []
    current = ""
    for card in cards:
        candidate = f"{current}\n\n{card}" if current else card
        if current and len(candidate) > MAX_MESSAGE_LENGTH:
            batches.append(current)
            current = card
        else:
            current = candidate
    if current:
        batches.append(current)

    for bot in get_bots().values():
        if not isinstance(bot, Bot):
            continue
        for group_id in groups:
            for batch in batches:
                try:
                    await bot.send_group_msg(group_id=group_id, message=batch)
                except Exception as exc:
                    log.warning(f"lkpet 日结算通知发送失败（群 {group_id}）: {exc}")
