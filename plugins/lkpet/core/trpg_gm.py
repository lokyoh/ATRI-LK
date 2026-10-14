"""跑团 GM：场景推进与三种形式的会话（任务书 §13.3）。

- **单人剧情**：bot 兼任 GM，章节场景图外置 trpg_chapters.yaml，自由文字行动
  （编号 / 关键词 / 技能名自由检定）推进；关键抉择写传记 ``key_choice``。
- **多人组队**：群内 2–5 人，队长代团队选行动；带检定的行动由**全体成员在 DEX
  降序下各自掷骰**，按多数结果决定分支（§13.3「按 DEX 轮流行动、各自检定」）。
  共享会话存 ``PETTRPG`` 的 ``party:<群号>`` 专用行。
- **短团本**：单场 ≤30 分钟（trpg.yaml），超时自动散团，无通关奖励。

通关发放少量成就点（trpg.yaml rewards），成长检定与传记写入都发生在**结束时刻**；
跑团的六维/技能只落 ``PETTRPG``，不触碰养成数值（§13.5 红线）。
"""

import random
from datetime import datetime
from typing import Any

from .trpg import (
    format_check,
    format_growth,
    get_or_create_card,
    growth_check,
    resolve_check,
)

PARTY_KEY_PREFIX = "party:"
"""组队会话在 PETTRPG 中的主键前缀（party:<群号>）。"""


def _load_trpg_config() -> dict[str, Any]:
    from ..config_loader import load_config

    return load_config("trpg")


def _load_chapters() -> dict[str, Any]:
    from ..config_loader import load_config

    cfg = _load_trpg_config()
    data = load_config(cfg.get("chapters", {}).get("file", "trpg_chapters"))
    return data.get("chapters", data)


def _text(key: str, default: str = "", **fields: Any) -> str:
    from ..utils.texts import text

    return text(f"trpg.{key}", default, **fields)


def party_key(group_id: int | str) -> str:
    return f"{PARTY_KEY_PREFIX}{group_id}"


def _is_party_key(player_id: str | int) -> bool:
    return str(player_id).startswith(PARTY_KEY_PREFIX)


# ---------------------------------------------------------------------------
# 会话创建
# ---------------------------------------------------------------------------

def _next_solo_chapter(card, cfg: dict[str, Any]) -> dict[str, Any] | None:
    done = card.chapter_progress.get("done", [])
    chapters = _load_chapters()
    for chapter_id in cfg.get("chapters", {}).get("order", []):
        if chapter_id not in done and chapter_id in chapters:
            return chapters[chapter_id] | {"id": chapter_id}
    return None


async def open_session(
    player_id: str | int,
    form: str,
    *,
    group_id: int | str | None = None,
    entry_hook=None,
    memory_hook=None,
) -> str:
    """开团（单人/组队/短团）。返回开场消息或拒绝原因。"""
    cfg = _load_trpg_config()
    if form not in ("solo", "party", "short"):
        return _text("usage", "用法：/宠物.跑团 开团单人 | 开团组队 | 开团短团 ……")
    if form == "party" and group_id is None:
        return _text("group_only", "组队团只能在群里发起")

    card = await get_or_create_card(player_id)
    if not card.attributes:
        return _text("no_card", "还没有跑团卡，先发送「/宠物.跑团 建卡」。")
    if card.session:
        return _text("session_active", "已有进行中的团，先「/宠物.跑团 结束」。")

    if form == "party":
        return await _open_party(player_id, group_id, cfg)

    # 单人 / 短团：每日通关次数限制（防刷，按开团时判定）。
    if form == "short":
        limit_slot = "short"
        limit = int(cfg.get("daily_limits", {}).get(limit_slot, 0))
        if limit and _daily_count(card, limit_slot) >= limit:
            return _text("daily_limit", "今日短团本通关已达上限（{limit} 次），明天再来。", limit=limit)

    if form == "solo":
        chapter = _next_solo_chapter(card, cfg)
        if chapter is None:
            return _text("chapters_done", "章节已全部通关。")
    else:
        chapters = _load_chapters()
        ids = [cid for cid in cfg.get("chapters", {}).get("short_ids", []) if cid in chapters]
        if not ids:
            return _text("chapters_done", "没有可用的短团本章节。")
        chosen_id = random.choice(ids)
        chapter = chapters[chosen_id] | {"id": chosen_id}

    session = {
        "form": form,
        "chapter": chapter["id"],
        "scene": chapter.get("start"),
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "used_skills": {str(player_id): []},
    }
    card.session = session
    card.updated_at = datetime.now()
    from ..database import save_trpg

    save_trpg(card)
    title = chapter.get("title", chapter["id"])
    header = (
        _text("open_solo", "📖 单人剧情开团：《{title}》", title=title)
        if form == "solo"
        else _text("open_short", "⏱ 短团本开团：《{title}》（限时 {minutes} 分钟）", title=title, minutes=int(cfg.get("forms", {}).get("short", {}).get("max_minutes", 30)))
    )
    return header + "\n" + _scene_text(chapter, session["scene"])


async def _display_name(player_id: str | int) -> str:
    """成员展示名：优先宠物名，兜底 player_id。"""
    from .pet_core import get_pet

    pet = await get_pet(str(player_id))
    return pet.name if pet is not None else str(player_id)


async def _open_party(player_id: str | int, group_id: int | str | None, cfg: dict[str, Any]) -> str:
    key = party_key(group_id)
    row = await get_or_create_card(key)
    party_cfg = cfg.get("forms", {}).get("party", {})
    min_members = int(party_cfg.get("min_members", 2))
    max_members = int(party_cfg.get("max_members", 5))

    own = await get_or_create_card(player_id)
    if not own.attributes:
        return _text("no_card", "还没有跑团卡，先发送「/宠物.跑团 建卡」。")
    own_name = await _display_name(player_id)

    if row.session:
        members: list[str] = [str(m) for m in row.session.get("members", [])]
        if str(player_id) in members:
            return _text("party_already", "你已经在队伍里了。")
        if len(members) >= max_members:
            return _text("party_full", "队伍已满（{max} 人）。", max=max_members)
        members.append(str(player_id))
        row.session["members"] = members
        row.session["member_names"][str(player_id)] = own_name
        row.session["member_dex"][str(player_id)] = int(own.attributes.get("DEX", 0))
        row.session.setdefault("used_skills", {})[str(player_id)] = []
        row.updated_at = datetime.now()
        from ..database import save_trpg

        save_trpg(row)
        chapter = _load_chapters().get(row.session.get("chapter", ""), {})
        return _text(
            "party_join",
            "✅ {name} 加入组队团（队长 {leader}）",
            name=own_name,
            leader=row.session.get("member_names", {}).get(row.session.get("leader", ""), row.session.get("leader", "")),
        ) + "\n" + _scene_text(chapter, row.session.get("scene"))

    chapters = _load_chapters()
    # 多人剧本从 trpg.yaml 的 chapters.party_ids（3 个）里随机取；
    # party_ids 缺失时回退到单人顺序的第一章。
    party_ids = [
        cid for cid in cfg.get("chapters", {}).get("party_ids", []) if cid in chapters
    ]
    if party_ids:
        chapter_id = random.choice(party_ids)
    else:
        order = cfg.get("chapters", {}).get("order", [])
        chapter_id = next((cid for cid in order if cid in chapters), None)
    if chapter_id is None:
        return _text("chapters_done", "没有可用章节。")
    chapter = chapters[chapter_id]

    row.session = {
        "form": "party",
        "chapter": chapter_id,
        "scene": chapter.get("start"),
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "leader": str(player_id),
        "members": [str(player_id)],
        "member_names": {str(player_id): own_name},
        "member_dex": {str(player_id): int(own.attributes.get("DEX", 0))},
        "used_skills": {str(player_id): []},
    }
    row.updated_at = datetime.now()
    from ..database import save_trpg

    save_trpg(row)
    return (
        _text(
            "party_open",
            "👥 组队团开团《{title}》（队长 {leader}，需 {min_members}-{max_members} 人）\n其他玩家发送「/宠物.跑团 开团 组队」加入。",
            title=chapter.get("title", chapter_id),
            leader=row.session["member_names"][str(player_id)],
            min_members=min_members,
            max_members=max_members,
        )
        + "\n"
        + _scene_text(chapter, row.session["scene"])
    )


# ---------------------------------------------------------------------------
# 行动输入
# ---------------------------------------------------------------------------

async def handle_input(
    player_id: str | int,
    raw: str,
    *,
    group_id: int | str | None = None,
    entry_hook=None,
    memory_hook=None,
) -> str:
    """自由文字/编号输入：优先本人会话，其次本群组队会话。"""
    card = await get_or_create_card(player_id)
    if card.session:
        return await _handle_solo(player_id, card, raw, entry_hook=entry_hook, memory_hook=memory_hook)
    if group_id is not None:
        row = await get_or_create_card(party_key(group_id))
        if row.session:
            return await _handle_party(player_id, row, raw, group_id=group_id, entry_hook=entry_hook, memory_hook=memory_hook)
    return _text("no_session", "当前没有进行中的团。发送「/宠物.跑团 开团单人」开始。")


def _match_choice(scene: dict[str, Any], raw: str) -> int | None:
    """编号或关键词匹配候选行动。"""
    choices = scene.get("choices", [])
    raw = (raw or "").strip()
    if raw.isdigit():
        number = int(raw)
        return number if 1 <= number <= len(choices) else None
    if len(raw) < 2:
        return None
    for idx, choice in enumerate(choices, start=1):
        text_ = str(choice.get("text", ""))
        if raw in text_ or (len(raw) >= 2 and text_ and text_ in raw):
            return idx
    return None


async def llm_rule(chapter: dict[str, Any], scene: dict[str, Any], raw: str, skills: list[str]) -> dict | None:
    """混合 GM 的 LLM 兜底：自由行动未命中模板时，交给 LLM 裁定。

    仅在玩家输入既不匹配编号/选项关键词、也不是技能名时调用（模板优先）。
    返回 ``{"skill", "difficulty", "on_success", "on_failure"}``；skill 为空串
    表示无需掷骰（on_success 作纯叙事展示）。失败、超时、输出不合法一律返回
    ``None``，由调用方回退到模板提示文案——**LLM 挂了也不影响跑团可玩性**。
    """
    import json as _json
    import re as _re

    prompt = (
        "你是一个文字跑团 GM 的行动裁定器。只输出一个 JSON 对象，"
        "不要输出任何其他文字、解释或代码围栏。\n"
        f"章节场景：{str(scene.get('text', '')).strip()}\n"
        f"玩家行动：「{raw.strip()}」\n"
        f"可用技能（只能从中选，或用空字符串表示该行动不需要检定）：{'、'.join(skills) or '（无）'}\n"
        '输出格式：{"skill": "<技能名或空字符串>", "difficulty": "普通|困难|极难", '
        '"on_success": "<≤50字的结果叙事>", "on_failure": "<≤50字的结果叙事>"}\n'
        "约束：叙事必须停留在当前场景内，不得直接推进剧情到下一场景、不得替玩家做出选择、"
        "不得提及规则术语（掷骰/数值/检定成功率）、不得复述本提示。"
    )
    try:
        import asyncio

        from ATRI.system.agent.llm import ModelType, llm_manager

        response = await asyncio.wait_for(
            llm_manager.call_model_by_type(ModelType.CHAT, prompt, "宠物-跑团"),
            timeout=15,
        )
        content = str(response.content).strip()
        content = _re.sub(r"^```(?:json)?\s*|\s*```$", "", content).strip()
        ruling = _json.loads(content)
        skill = str(ruling.get("skill", "") or "").strip()
        difficulty = str(ruling.get("difficulty", "普通") or "普通")
        if difficulty not in ("普通", "困难", "极难"):
            difficulty = "普通"
        return {
            "skill": skill,
            "difficulty": difficulty,
            "on_success": str(ruling.get("on_success", "") or ""),
            "on_failure": str(ruling.get("on_failure", "") or ""),
        }
    except Exception:  # 网络/超时/JSON/字段异常都走模板兜底
        return None


async def _handle_solo(player_id: str | int, card, raw: str, *, entry_hook=None, memory_hook=None) -> str:
    cfg = _load_trpg_config()
    session = card.session
    chapters = _load_chapters()
    chapter = chapters.get(session.get("chapter", ""), None)
    if chapter is None:
        return await _settle(card, [str(player_id)], cleared=False, form=session.get("form", "solo"), entry_hook=entry_hook, memory_hook=memory_hook)

    if session.get("form") == "short":
        expired = _short_expired(session, cfg)
        if expired:
            return await _settle(card, [str(player_id)], cleared=False, form="short", entry_hook=entry_hook, memory_hook=memory_hook)

    scene = chapter.get("scenes", {}).get(session.get("scene"), {})
    number = _match_choice(scene, raw)
    if number is None:
        # 自由检定：输入恰为技能名时直接掷骰（场景不变）。
        skill_name = (raw or "").strip()
        if skill_name in card.skills:
            session.setdefault("used_skills", {}).setdefault(str(player_id), []).append(skill_name)
            result = resolve_check(card.skills[skill_name], "普通")
            _persist_session(card)
            return (
                format_check(skill_name, card.skills[skill_name], result)
                + "\n"
                + _scene_text(chapter, session.get("scene"))
            )
        fallback = _text("fallback", "无法理解这个行动。可回复编号，或输入技能名自由检定。") + "\n" + _scene_text(chapter, session.get("scene"))
        # 模板未命中 → 混合 GM：交给 LLM 裁定；失败/超时/不合法回退模板提示。
        ruling = await llm_rule(chapter, scene, raw, list(card.skills))
        if ruling is None:
            return fallback
        if not ruling["skill"]:
            # 纯叙事裁定：不掷骰，留在当前场景。
            return (ruling["on_success"] or fallback.splitlines()[0]) + "\n" + _scene_text(chapter, session.get("scene"))
        if ruling["skill"] not in card.skills:
            return fallback  # LLM 选了卡外技能：按提示兕底
        session.setdefault("used_skills", {}).setdefault(str(player_id), []).append(ruling["skill"])
        result = resolve_check(card.skills[ruling["skill"]], ruling["difficulty"])
        _persist_session(card)
        narrative = ruling["on_success"] if result["passed"] else ruling["on_failure"]
        lines = [format_check(ruling["skill"], card.skills[ruling["skill"]], result)]
        if narrative:
            lines.append(narrative)
        return "\n".join(lines) + "\n" + _scene_text(chapter, session.get("scene"))

    choice = scene.get("choices", [])[number - 1]
    lines: list[str] = []

    check_spec = choice.get("check")
    target_scene = choice.get("next")
    if check_spec:
        skill_name = str(check_spec.get("skill", ""))
        skill_value = int(card.skills.get(skill_name, 0))
        difficulty = str(check_spec.get("difficulty", "普通"))
        result = resolve_check(skill_value, difficulty)
        passed = result["passed"]
        target_scene = str(check_spec.get("success") if passed else check_spec.get("failure", check_spec.get("success")))
        session.setdefault("used_skills", {}).setdefault(str(player_id), []).append(skill_name)
        lines.append(format_check(skill_name, skill_value, result, passed=passed))

    if choice.get("key"):
        lines.append(
            await _write_key_choice(
                entry_hook,
                player_id,
                chapter,
                str(choice.get("text", "")),
            )
        )

    session["scene"] = target_scene
    _persist_session(card)

    if session.get("form") == "short":
        expired = _short_expired(session, cfg)
        if expired:
            return "\n".join(line for line in lines if line) + "\n" + await _settle(
                card, [str(player_id)], cleared=False, form="short", entry_hook=entry_hook, memory_hook=memory_hook
            )

    return "\n".join(line for line in lines if line) + "\n" + await _after_move(card, chapter, session, [str(player_id)], entry_hook=entry_hook, memory_hook=memory_hook)


async def _handle_party(player_id: str | int, row, raw: str, *, group_id=None, entry_hook=None, memory_hook=None) -> str:
    cfg = _load_trpg_config()
    session = row.session
    leader = str(session.get("leader", ""))
    members = [str(m) for m in session.get("members", [])]
    if str(player_id) != leader:
        return _text(
            "party_wait_leader",
            "⏳ 等待队长决策（当前队长：{leader}）。",
            leader=session.get("member_names", {}).get(leader, leader),
        )

    chapters = _load_chapters()
    chapter = chapters.get(session.get("chapter", ""), None)
    if chapter is None:
        return await _settle_party(row, cleared=False, entry_hook=entry_hook, memory_hook=memory_hook)

    scene = chapter.get("scenes", {}).get(session.get("scene"), {})
    number = _match_choice(scene, raw)
    if number is None:
        return _text("fallback", "队长可回复编号选择行动。") + "\n" + _scene_text(chapter, session.get("scene"))

    choice = scene.get("choices", [])[number - 1]
    lines: list[str] = []

    check_spec = choice.get("check")
    target_scene = choice.get("next")
    if check_spec:
        skill_name = str(check_spec.get("skill", ""))
        difficulty = str(check_spec.get("difficulty", "普通"))
        # 全体成员按 DEX 降序各自检定，多数结果决定分支（§13.3）。
        ordered = sorted(members, key=lambda m: -int(session.get("member_dex", {}).get(m, 0)))
        results: list[tuple[str, bool]] = []
        roll_lines: list[str] = []
        for member in ordered:
            member_card = await get_or_create_card(member)
            skill_value = int(member_card.skills.get(skill_name, 0))
            result = resolve_check(skill_value, difficulty)
            results.append((member, result["passed"]))
            session.setdefault("used_skills", {}).setdefault(member, []).append(skill_name)
            name = session.get("member_names", {}).get(member, member)
            roll_lines.append(f"{name}：" + format_check(skill_name, skill_value, result).split("：", 1)[-1])
        passed = sum(1 for _, ok in results if ok) * 2 > len(results)
        target_scene = str(check_spec.get("success") if passed else check_spec.get("failure", check_spec.get("success")))
        lines.append(
            _text(
                "party_check",
                "🎲 组队检定《{skill}》（{difficulty}，DEX 序）：",
                skill=skill_name,
                difficulty=difficulty,
            )
        )
        lines.extend(roll_lines)
        lines.append(
            _text("party_majority_pass", "→ 多数通过，进入成功分支。")
            if passed
            else _text("party_majority_fail", "→ 多数未过，落入失败分支。")
        )

    if choice.get("key"):
        # 组队关键抉择：队长代记（传记仍写队长自己的宠物）。
        content = await _write_key_choice(entry_hook, player_id, chapter, str(choice.get("text", "")))
        if content:
            lines.append(content)

    session["scene"] = target_scene
    row.updated_at = datetime.now()
    from ..database import save_trpg

    save_trpg(row)

    return "\n".join(line for line in lines if line) + "\n" + await _after_move_party(
        row, chapter, session, entry_hook=entry_hook, memory_hook=memory_hook
    )


# ---------------------------------------------------------------------------
# 场景推进与结算
# ---------------------------------------------------------------------------

def _scene_text(chapter: dict[str, Any], scene_id: Any) -> str:
    scene = chapter.get("scenes", {}).get(scene_id, {})
    if scene.get("end"):
        return scene.get("text", "")
    lines = [
        _text("scene_header", "📖 {title}", title=chapter.get("title", "")),
        scene.get("text", ""),
    ]
    for idx, choice in enumerate(scene.get("choices", []), start=1):
        lines.append(f"{idx}. {choice.get('text', '')}")
    lines.append(_text("scene_reply", "回复编号行动；也可输入技能名自由检定。"))
    return "\n".join(line for line in lines if line)


async def _after_move(card, chapter, session, members: list[str], *, entry_hook=None, memory_hook=None) -> str:
    """单人/短团移动后的响应：终局则结算，否则展示新场景。"""
    scene = chapter.get("scenes", {}).get(session.get("scene"), {})
    if scene.get("end"):
        settled = await _sync_finish(card, members, chapter, session, entry_hook=entry_hook, memory_hook=memory_hook)
        return scene.get("text", "") + "\n" + settled
    return _scene_text(chapter, session.get("scene"))


async def _after_move_party(row, chapter, session, *, entry_hook=None, memory_hook=None) -> str:
    scene = chapter.get("scenes", {}).get(session.get("scene"), {})
    if scene.get("end"):
        return scene.get("text", "") + "\n" + await _settle_party(row, cleared=True, entry_hook=entry_hook, memory_hook=memory_hook)
    return _scene_text(chapter, session.get("scene"))


async def _sync_finish(card, members: list[str], chapter, session, *, entry_hook=None, memory_hook=None) -> str:
    """单人/短团：终局结算并返回结算文案。"""
    return await _settle(
        card,
        members,
        cleared=True,
        form=session.get("form", "solo"),
        chapter=chapter,
        entry_hook=entry_hook,
        memory_hook=memory_hook,
    )


async def end_session(player_id: str | int, *, group_id: int | str | None = None, entry_hook=None, memory_hook=None) -> str:
    """主动结束当前团（成长照常；通关奖励只在通关时发）。"""
    card = await get_or_create_card(player_id)
    if card.session:
        chapters = _load_chapters()
        chapter = chapters.get(card.session.get("chapter", ""))
        return await _settle(
            card,
            [str(player_id)],
            cleared=bool(chapter and chapter.get("scenes", {}).get(card.session.get("scene"), {}).get("end")),
            form=card.session.get("form", "solo"),
            chapter=chapter,
            entry_hook=entry_hook,
            memory_hook=memory_hook,
        )
    if group_id is not None:
        row = await get_or_create_card(party_key(group_id))
        if row.session:
            if str(row.session.get("leader", "")) != str(player_id):
                return _text("party_not_leader", "只有队长可以结束组队团。")
            chapters = _load_chapters()
            chapter = chapters.get(row.session.get("chapter", ""))
            cleared = bool(chapter and chapter.get("scenes", {}).get(row.session.get("scene"), {}).get("end"))
            return await _settle_party(row, cleared=cleared, entry_hook=entry_hook, memory_hook=memory_hook)
    return _text("no_session", "当前没有进行中的团。发送「/宠物.跑团 开团单人」开始。")


async def _settle(
    card,
    members: list[str],
    *,
    cleared: bool,
    form: str,
    chapter: dict[str, Any] | None = None,
    entry_hook=None,
    memory_hook=None,
) -> str:
    """结算单人/短团：成长 → 章节进度 → 通关奖励 → 传记 → 清会话。"""
    cfg = _load_trpg_config()
    session = card.session or {}
    member_id = str(members[0])
    lines: list[str] = []

    used = list((session.get("used_skills") or {}).get(member_id, []))
    results = growth_check(card, used, cfg=cfg) if card.attributes else []
    if results:
        lines.append(format_growth(results))

    title = (chapter or {}).get("title", session.get("chapter", ""))

    # 结局好坏由场景的 outcome 标记（trpg_chapters.yaml）：坏结局不发成就点，
    # 成长与传记照常（失败有重量，但成长不受罚）。
    outcome = "good"
    if cleared and chapter and session.get("scene"):
        outcome = str(
            chapter.get("scenes", {}).get(session.get("scene"), {}).get("outcome", "good")
        )
    is_bad = outcome == "bad"

    if cleared and form == "solo" and chapter:
        done = card.chapter_progress.setdefault("done", [])
        chapter_id = session.get("chapter")
        if chapter_id and chapter_id not in done:
            done.append(str(chapter_id))
        if is_bad:
            lines.append(
                _text("outcome_bad", "💔 坏结局《{title}》：不发放成就点（成长与传记照常）。", title=title)
            )
            lines.append(
                await _write_memory(memory_hook, member_id, _text("memory_failed", "跑团失手：《{title}》", title=title))
            )
        else:
            points = int(cfg.get("rewards", {}).get("solo_chapter", 0))
            lines.append(_text("cleared", "✅ 章节通关《{title}》！成就点 +{points}", title=title, points=points))
            lines.append(await _award_points(member_id, points))
            lines.append(
                await _write_memory(memory_hook, member_id, _text("memory_solo", "跑团通关：《{title}》", title=title))
            )
    elif cleared and form == "short":
        if is_bad:
            lines.append(
                _text("outcome_bad", "💔 坏结局《{title}》：不发放成就点（成长与传记照常）。", title=title)
            )
            lines.append(
                await _write_memory(memory_hook, member_id, _text("memory_failed", "跑团失手：《{title}》", title=title))
            )
        else:
            limit = int(cfg.get("daily_limits", {}).get("short", 0))
            if limit and _daily_count(card, "short") >= limit:
                lines.append(_text("daily_limit_reward", "✅ 短团本通关《{title}》（今日次数已达上限，未发放成就点）", title=title))
            else:
                points = int(cfg.get("rewards", {}).get("short_run", 0))
                lines.append(_text("cleared", "✅ 短团本通关《{title}》！成就点 +{points}", title=title, points=points))
                lines.append(await _award_points(member_id, points))
                _bump_daily(card, "short")
            lines.append(
                await _write_memory(memory_hook, member_id, _text("memory_short", "短团本通关：《{title}》", title=title))
            )
    else:
        lines.append(_text("quit", "已结束本场团。"))

    card.session = {}
    card.updated_at = datetime.now()
    from ..database import save_trpg

    save_trpg(card)
    return "\n".join(line for line in lines if line)


async def _settle_party(row, *, cleared: bool, entry_hook=None, memory_hook=None) -> str:
    """结算组队团：每位成员各自成长、奖励、传记。"""
    cfg = _load_trpg_config()
    session = row.session or {}
    members = [str(m) for m in session.get("members", [])]
    chapters = _load_chapters()
    chapter = chapters.get(session.get("chapter", ""))
    title = (chapter or {}).get("title", session.get("chapter", ""))
    # 结局好坏（trpg_chapters.yaml 的 outcome）：坏结局全员不发成就点。
    outcome = "good"
    if cleared and chapter and session.get("scene"):
        outcome = str(
            chapter.get("scenes", {}).get(session.get("scene"), {}).get("outcome", "good")
        )
    is_bad = outcome == "bad"
    lines: list[str] = []

    for member in members:
        member_card = await get_or_create_card(member)
        used = list((session.get("used_skills") or {}).get(member, []))
        results = growth_check(member_card, used, cfg=cfg) if member_card.attributes else []
        name = session.get("member_names", {}).get(member, member)
        if results:
            lines.append(f"{name}：" + format_growth(results).replace("\n", " "))
        if cleared:
            if is_bad:
                lines.append(
                    _text("memory_failed_member", "{name}：跑团失手（坏结局不发成就点）", name=name)
                )
                lines.append(
                    await _write_memory(memory_hook, member, _text("memory_failed", "跑团失手：《{title}》", title=title))
                )
                continue
            limit = int(cfg.get("daily_limits", {}).get("party", 0))
            if limit and _daily_count(member_card, "party") >= limit:
                lines.append(_text("daily_limit_reward", "✅ 通关《{title}》（{name} 今日次数已达上限，未发放成就点）", title=title, name=name))
            else:
                points = int(cfg.get("rewards", {}).get("party_member", 0))
                lines.append(_text("party_award", "{name} 获得成就点 +{points}", name=name, points=points))
                lines.append(await _award_points(member, points))
                _bump_daily(member_card, "party")
                member_card.updated_at = datetime.now()
                from ..database import save_trpg

                save_trpg(member_card)
            lines.append(
                await _write_memory(memory_hook, member, _text("memory_party", "组队跑团通关：《{title}》", title=title))
            )

    row.session = {}
    row.updated_at = datetime.now()
    from ..database import save_trpg

    save_trpg(row)
    if not cleared:
        lines.insert(0, _text("quit", "已结束本场团。"))
    elif is_bad:
        lines.insert(0, _text("outcome_bad", "💔 坏结局《{title}》：不发放成就点（成长与传记照常）。", title=title))
    else:
        lines.insert(0, _text("party_cleared", "✅ 组队团通关《{title}》", title=title))
    return "\n".join(line for line in lines if line)


# ---------------------------------------------------------------------------
# 进度查询与工具
# ---------------------------------------------------------------------------

def progress_text(card) -> str:
    """/宠物.跑团 进度：章节进度、战绩与当前会话。"""
    cfg = _load_trpg_config()
    done = card.chapter_progress.get("done", [])
    lines = [
        _text(
            "progress",
            "📜 跑团进度：已通关 {done} 个章节｜短团 {short} 场｜组队 {party} 场",
            done=len(done),
            short=int(card.stats.get("short_clears", 0)),
            party=int(card.stats.get("party_clears", 0)),
        )
    ]
    if card.session:
        chapter = _load_chapters().get(card.session.get("chapter", {}), {})
        lines.append(
            _text(
                "progress_active",
                "进行中：《{title}》（{form}）",
                title=chapter.get("title", card.session.get("chapter", "")),
                form=card.session.get("form", ""),
            )
        )
    elif not card.attributes:
        lines.append(_text("no_card", "还没有跑团卡，先发送「/宠物.跑团 建卡」。"))
    return "\n".join(lines)


async def standalone_check(player_id: str | int, skill_name: str) -> str:
    """/宠物.跑团 检定 <技能>：独立掷骰（团内会记入成长）。"""
    card = await get_or_create_card(player_id)
    if not card.attributes:
        return _text("no_card", "还没有跑团卡，先发送「/宠物.跑团 建卡」。")
    skill_name = skill_name.strip()
    if skill_name not in card.skills:
        return _text(
            "unknown_skill",
            "没有名为「{skill}」的技能。可用：{skills}",
            skill=skill_name,
            skills=" / ".join(sorted(card.skills)),
        )
    if card.session:
        card.session.setdefault("used_skills", {}).setdefault(str(player_id), []).append(skill_name)
        _persist_session(card)
    result = resolve_check(card.skills[skill_name], "普通")
    return format_check(skill_name, card.skills[skill_name], result)


def _short_expired(session: dict[str, Any], cfg: dict[str, Any]) -> bool:
    try:
        started = datetime.fromisoformat(str(session.get("started_at")))
    except ValueError:
        return False
    minutes = int(cfg.get("forms", {}).get("short", {}).get("max_minutes", 30))
    return (datetime.now() - started).total_seconds() > minutes * 60


def _daily_count(card, slot: str) -> int:
    from datetime import date

    today = date.today().isoformat()
    if card.stats.get(f"{slot}_date") != today:
        return 0
    return int(card.stats.get(f"{slot}_count", 0))


def _bump_daily(card, slot: str) -> None:
    from datetime import date

    today = date.today().isoformat()
    if card.stats.get(f"{slot}_date") != today:
        card.stats[f"{slot}_date"] = today
        card.stats[f"{slot}_count"] = 0
    card.stats[f"{slot}_count"] = int(card.stats.get(f"{slot}_count", 0)) + 1
    card.stats[f"{slot}_clears"] = int(card.stats.get(f"{slot}_clears", 0)) + 1


async def _award_points(player_id: str | int, points: int) -> str:
    if points <= 0:
        return ""
    from .reincarnation import get_or_create_reincarnation
    from ..database import save_reincarnation

    reinc = await get_or_create_reincarnation(str(player_id))
    reinc.achievement_points += points
    reinc.updated_at = datetime.now()
    save_reincarnation(reinc)
    return _text(
        "award",
        "🏅 成就点 +{points}（累计 {total}）",
        points=points,
        total=reinc.achievement_points,
    )


async def _write_key_choice(entry_hook, player_id, chapter, choice_text: str) -> str:
    if entry_hook is None or not choice_text:
        return ""
    from ..utils.callbacks import run_callback
    from .pet_core import get_pet

    pet = await get_pet(str(player_id))
    if pet is None:
        return ""
    content = _text(
        "bio_key",
        "跑团关键抉择：《{title}》 —— {choice}",
        title=chapter.get("title", chapter.get("id", "")),
        choice=choice_text,
    )
    await run_callback(entry_hook, pet, {"type": "key_choice", "content": content})
    return content


async def _write_memory(memory_hook, player_id, content: str) -> str:
    if memory_hook is None or not content:
        return ""
    from ..utils.callbacks import run_callback
    from .pet_core import get_pet

    pet = await get_pet(str(player_id))
    if pet is None:
        return ""
    await run_callback(memory_hook, pet, {"content": content})
    return _text("memory_written", "📖 已写入传记：{content}", content=content)


def _persist_session(card) -> None:
    card.updated_at = datetime.now()
    from ..database import save_trpg

    save_trpg(card)
