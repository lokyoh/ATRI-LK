"""结算：成就点、经验、首通解锁、历史统计。"""

from datetime import datetime
from typing import Any

from ._common import (
    _load_rogue_config,
    _load_skills_config,
    _persist,
    _text,
)


async def settle_run(
    pet,
    rogue,
    *,
    reason: str = "retreat",
    rogue_config: dict[str, Any] | None = None,
    title_hook=None,
) -> str:
    """结束本局：按到达层数发成就点、经验写回、首通解锁写回，清空 run。

    这是 run 内容触碰养成数据的**唯一入口**（§12.4 红线）。
    """
    cfg = rogue_config if rogue_config is not None else _load_rogue_config()
    run = rogue.run_state or {}
    floors_reached = int(run.get("floor", 0))
    boss_wins = int(run.get("boss_wins", 0))
    cleared = bool(run.get("cleared", False))

    rewards = cfg.get("rewards", {})
    points = floors_reached * int(rewards.get("per_floor", 0))
    points += boss_wins * int(rewards.get("boss_bonus", 0))
    if cleared:
        points += int(rewards.get("clear_bonus", 0))

    lines = [
        _text(
            "settle_header",
            "📊 本局结算：到达第 {floor} 层{cleared}",
            floor=floors_reached,
            cleared=_text("settle_cleared", "，通关！") if cleared else "",
        )
    ]

    # 1) 经验写回并允许升级。
    exp_gain = int(run.get("exp", 0))
    if exp_gain > 0:
        from ..leveling import check_level_up

        pet.exp += exp_gain
        level_result = check_level_up(pet)
        lines.append(_text("settle_exp", "经验 +{exp}", exp=exp_gain))
        if level_result.get("level_up"):
            lines.append(
                _text(
                    "settle_level",
                    "🎉 升到 {level} 级！获得 {talent_points} 天赋点",
                    level=level_result["new_level"],
                    talent_points=pet.talent_points,
                )
            )

    # 2) 首通解锁写回（幂等）。
    skills_cfg = _load_skills_config()
    for floor_key in dict.fromkeys(run.get("unlocks", [])):
        spec = (cfg.get("boss_unlocks") or {}).get(str(floor_key))
        if spec is None or floor_key in rogue.unlocked:
            continue
        rogue.unlocked.append(floor_key)
        kind = spec.get("type")
        if kind == "equipment":
            slot = spec.get("slot", "misc")
            pet.equipment[slot] = spec.get("item", {})
            lines.append(
                _text(
                    "unlock_equipment",
                    "🔓 首通解锁装备：{name}",
                    name=spec.get("item", {}).get("name", slot),
                )
            )
        elif kind == "skill":
            from ..skills import add_skill

            skill_id = spec.get("id", "")
            skill_name = (
                skills_cfg.get("skills", {}).get(skill_id, {}).get("name", skill_id)
            )
            if add_skill(pet, skill_id, skills_cfg):
                lines.append(
                    _text("unlock_skill", "🔓 首通解锁技能：{name}", name=skill_name)
                )
            else:
                lines.append(
                    _text(
                        "unlock_skill_full",
                        "🔓 已记录首通技能 {name}（技能槽已满，暂时无法学会）",
                        name=skill_name,
                    )
                )
        elif kind == "title":
            pet.title = spec.get("title", pet.title)
            lines.append(
                _text(
                    "unlock_title",
                    "🔓 首通解锁称号：{title}",
                    title=spec.get("title", ""),
                )
            )
            if title_hook is not None:
                from ...utils.callbacks import run_callback

                await run_callback(title_hook, pet, spec.get("title", ""))

    # 3) 成就点写回轮回数据。
    if points > 0:
        from ..reincarnation import get_or_create_reincarnation

        reinc = await get_or_create_reincarnation(pet.player_id)
        reinc.achievement_points += points
        reinc.updated_at = datetime.now()
        from ...database import save_reincarnation

        save_reincarnation(reinc)
        lines.append(
            _text(
                "settle_points",
                "成就点 +{points}（累计 {total}）",
                points=points,
                total=reinc.achievement_points,
            )
        )

    # 4) 历史统计与 run 清空。
    rogue.total_runs += 1
    rogue.best_floor = max(rogue.best_floor, floors_reached)
    if cleared:
        rogue.clears += 1
    rogue.run_state = {}
    rogue.updated_at = datetime.now()
    await _persist(rogue)

    if reason == "retreat" and not cleared:
        lines.insert(0, _text("retired", "🏳️ 已撤退。"))
    return "\n".join(line for line in lines if line)
