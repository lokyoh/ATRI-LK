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
            learned = level_result.get("learned_skills") or []
            if learned:
                lines.append(
                    _text(
                        "settle_grant_skill",
                        "📖 升级奖励技能：{names}",
                        names="、".join(
                            str(item.get("name") or item.get("id", ""))
                            for item in learned
                        ),
                    )
                )

    # 2) 首通解锁写回（幂等）。键有三种：
    #    "20"/"40"…            首领层首通（boss_unlocks：装备 / 称号）
    #    "elite:<区域id>"      该区域首次击败精英（stage_skill_rewards.<stage>.elite）
    #    "boss:<区域id>"       该区域首次击败首领（stage_skill_rewards.<stage>.boss）
    skills_cfg = _load_skills_config()
    for raw_key in dict.fromkeys(run.get("unlocks", [])):
        floor_key = str(raw_key)
        if floor_key in rogue.unlocked:
            continue
        spec = _unlock_spec(cfg, floor_key)
        if spec is None:
            continue
        rogue.unlocked.append(floor_key)
        lines.extend(await _grant_unlock(pet, spec, skills_cfg, floor_key, title_hook))

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


def _unlock_spec(cfg: dict[str, Any], unlock_key: str) -> dict[str, Any] | None:
    """把首通记录的键翻译成奖励描述。

    ``elite:<区域>`` / ``boss:<区域>`` → ``stage_skill_rewards`` 里的技能；
    纯数字键 → ``boss_unlocks`` 里的装备 / 称号。
    """
    if ":" in unlock_key:
        kind, _, stage_id = unlock_key.partition(":")
        if kind not in ("elite", "boss"):
            return None
        stage = (cfg.get("stage_skill_rewards") or {}).get(stage_id)
        if not isinstance(stage, dict):
            return None
        skill_id = stage.get(kind)
        if not skill_id:
            return None
        return {"type": "skill", "id": str(skill_id), "stage": stage_id, "kind": kind}
    spec = (cfg.get("boss_unlocks") or {}).get(unlock_key)
    return spec if isinstance(spec, dict) else None


async def _grant_unlock(
    pet,
    spec: dict[str, Any],
    skills_cfg: dict[str, Any],
    unlock_key: str,
    title_hook=None,
) -> list[str]:
    """发放一条首通奖励，返回要拼进结算消息的行。"""
    from ..skills import grant_skill

    kind = spec.get("type")
    if kind == "equipment":
        slot = spec.get("slot", "misc")
        pet.equipment[slot] = spec.get("item", {})
        return [
            _text(
                "unlock_equipment",
                "🔓 首通解锁装备：{name}",
                name=spec.get("item", {}).get("name", slot),
            )
        ]
    if kind == "title":
        pet.title = spec.get("title", pet.title)
        if title_hook is not None:
            from ...utils.callbacks import run_callback

            await run_callback(title_hook, pet, spec.get("title", ""))
        return [
            _text(
                "unlock_title", "🔓 首通解锁称号：{title}", title=spec.get("title", "")
            )
        ]
    if kind == "skill":
        granted = grant_skill(pet, spec.get("id", ""), skills_cfg, source="rogue")
        if not granted["added"]:
            return [
                _text(
                    "unlock_skill_known",
                    "🔓 首通技能「{name}」已在技能库中。",
                    name=granted["name"],
                )
            ]
        key = (
            "unlock_boss_skill"
            if unlock_key.startswith("boss:")
            else "unlock_elite_skill"
        )
        default = (
            "🔓 首次击败首领解锁技能：{name}（已进入技能库）"
            if unlock_key.startswith("boss:")
            else "🔓 首次击败精英解锁技能：{name}（已进入技能库）"
        )
        line = _text(key, default, name=granted["name"])
        if not granted["equipped"]:
            line += _text(
                "unlock_skill_bench", "（技能槽已满，请在「/宠物.技能」中装备）"
            )
        return [line]
    return []
