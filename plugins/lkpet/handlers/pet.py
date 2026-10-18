"""Command handlers for the raising system.

Wiring notes:

* ``core.daily`` takes the biography and reincarnation systems as callbacks, so
  the settlement here passes ``biography.add_daily_log`` and
  ``reincarnation.check_reincarnation_available``.
* ``core.reincarnation`` takes ``biography.add_biography_entry`` the same way, which
  is also what fills ``Biography.ending``.
* Reincarnation is the only path that ends a cycle, and it always requires the
  literal ``确认`` argument.
"""

from random import choice

from nonebot.adapters.onebot.v11 import Bot, Event, Message
from nonebot.adapters.onebot.v11.helpers import Cooldown
from nonebot.params import CommandArg, Depends

from ATRI.log import log
from ATRI.message import img_msg
from ATRI.system.htmlrender import text_to_pic
from ATRI.system.lkapi.bot import util as lk_util
from ATRI.system.lkapi.bot.checker import (
    is_chat_switch_on,
    is_lk_user,
    not_safe_mode,
)

from .. import config as lkpet_config
from ..core import (
    biography,
    daily,
    interaction,
    pet_core,
    reincarnation,
    roguelike,
    skills,
    talents,
    tutorial,
)
from ..core.affection import get_affection_level
from ..core.chat import chat_manager
from ..database import load_pet_names
from ..matchers import (
    pet_all_list_matcher,
    pet_bathe_matcher,
    pet_bio_matcher,
    pet_chat_matcher,
    pet_create_matcher,
    pet_daily_matcher,
    pet_feed_matcher,
    pet_group_list_matcher,
    pet_instruction_matcher,
    pet_log_matcher,
    pet_love_matcher,
    pet_perks_matcher,
    pet_play_matcher,
    pet_reincarnate_matcher,
    pet_rename_matcher,
    pet_rest_matcher,
    pet_rogue_matcher,
    pet_skill_matcher,
    pet_status_matcher,
    pet_talent_matcher,
    pet_train_matcher,
)
from ..utils.formatters import (
    format_biography,
    format_daily_logs,
    format_daily_report,
    format_perk_list,
    format_pet_status,
    format_reincarnation_check,
    format_reincarnation_result,
    format_skill_list,
    format_talent_list,
)
from ..utils.texts import apply_command_start, text, text_list

CONFIRM_WORD = "确认"
"""``/宠物.轮回 确认`` is the only way to end a cycle."""

PAGE_SIZE = 20
"""Entries per message for the two pet-listing commands."""

_COOLDOWN_NOTICES = text_list("cooldown.notices", ["冷静一下"])
"""Prompts shown when a command is used faster than its cooldown allows."""

_ROGUE_USAGE_HINT = (
    "用法：/宠物.肉鸽 开始｜继续｜进度｜撤退\n进入层数后按提示回复编号应答。"
)


def _never_empty(message: str, fallback: str) -> str:
    """防空回复：texts.yaml 缺键（部署未同步 res/ 配置）时 ``text(key, "")`` 会得到
    空串，QQ 端把空消息显示为「该消息类型暂不支持查看」。空串一律降级为带当前
    指令前缀的兜底文案，绝不发空消息。"""
    if message and message.strip():
        return message
    return apply_command_start(fallback)


def _cooldown_notice() -> str:
    return choice(_COOLDOWN_NOTICES)


async def _guard(matcher) -> None:
    """Refuse every command while the raising system is switched off."""
    if not lkpet_config.get("enable"):
        await matcher.finish(text("status.disabled", "养成系统当前已关闭。"))


async def _load_pet(matcher, player_id: int):
    """Return the caller's pet, or finish with a hint when they have none."""
    pet = await pet_core.get_pet(player_id)
    if pet is None:
        await matcher.finish(
            text("common.no_pet", "请先创建宠物：/宠物.创建 <性格> <名字>")
        )
    return pet


async def _settle(matcher, pet, player_id: int) -> dict:
    """Run the day settlement with biography and reincarnation wired in."""
    result = await daily.check_daily_reset(
        pet,
        daily_log_writer=biography.add_daily_log,
        reincarnation_checker=reincarnation.check_reincarnation_available,
    )
    if result.get("need_settle"):
        await pet_core.save_pet(pet)
    return result


async def _tutorial_prompt(pet, event: str) -> str | None:
    """Advance the tutorial on ``event`` and return the next hint, if any."""
    if not lkpet_config.get("tutorial_enabled"):
        return None
    step = await tutorial.check_tutorial_trigger(pet, event)
    if step is None:
        return None
    hint = text("tutorial.hint", "", text=step["text"])
    if step["id"] == "tutorial_done":
        await tutorial.advance_tutorial(pet)
    return hint


def _compose(*parts: str | None) -> str:
    return "\n\n".join(part for part in parts if part)


@pet_create_matcher.handle()
async def handle_create(bot: Bot, event: Event, args: Message = CommandArg()):
    await _guard(pet_create_matcher)
    player_id = int(event.get_user_id())

    parts = args.extract_plain_text().strip().split()
    if len(parts) < 2:
        await pet_create_matcher.finish(
            text(
                "create.usage",
                "用法：/宠物.创建 <性格> <名字> [设定]",
                personalities=" / ".join(pet_core.describe_personalities()),
            )
        )
    raw_personality, name = parts[0], parts[1]
    # 旧版「领养」的「设定」也在这里接收，不再走二次追问流程。
    instruction = pet_core.clean_instruction(" ".join(parts[2:]))

    try:
        # 性格接受中文名（勇敢）与英文键（brave），统一解析成配置键存库。
        personality = pet_core.resolve_personality(raw_personality)
        name = pet_core.clean_and_validate_name(name)
        pet = await pet_core.create_pet(
            player_id, personality, name, instruction=instruction
        )
    except ValueError as exc:
        await pet_create_matcher.finish(f"❌ {exc}")

    await tutorial.start_tutorial(pet)
    for _ in range(3):  # welcome / choose_personality / choose_name 已由创建流程完成
        await tutorial.advance_tutorial(pet)
    await pet_core.save_pet(pet)

    attributes = pet.attributes
    # 注意：属性名 def / spd 里 def 是 Python 关键字，只能走字典展开。
    create_fields = {
        "name": pet.name,
        "personality": pet_core.personality_display(pet.personality),
        "level": pet.level,
        "hp": attributes.get("hp", 0),
        "atk": attributes.get("atk", 0),
        "def": attributes.get("def", 0),
        "spd": attributes.get("spd", 0),
        "mp": attributes.get("mp", 0),
    }
    await pet_create_matcher.finish(
        _compose(
            text("create.ok", "🎉 宠物创建成功！", **create_fields),
            await _tutorial_prompt(pet, "welcome"),
        )
    )


@pet_status_matcher.handle()
async def handle_status(bot: Bot, event: Event):
    await _guard(pet_status_matcher)
    player_id = int(event.get_user_id())
    pet = await _load_pet(pet_status_matcher, player_id)

    settle = await _settle(pet_status_matcher, pet, player_id)
    hint = await _tutorial_prompt(pet, "first_status")
    await pet_core.save_pet(pet)

    report = format_daily_report(settle) if settle.get("need_settle") else None
    await pet_status_matcher.finish(_compose(report, format_pet_status(pet), hint))


@pet_daily_matcher.handle()
async def handle_daily(bot: Bot, event: Event):
    await _guard(pet_daily_matcher)
    player_id = int(event.get_user_id())
    pet = await _load_pet(pet_daily_matcher, player_id)

    settle = await _settle(pet_daily_matcher, pet, player_id)
    if not settle.get("need_settle"):
        await pet_daily_matcher.finish(text("daily.no_settle", "今天已经结算过了"))

    hint = await _tutorial_prompt(pet, "day1_summary")
    await pet_core.save_pet(pet)
    await pet_daily_matcher.finish(_compose(format_daily_report(settle), hint))


@pet_feed_matcher.handle()
async def handle_feed(bot: Bot, event: Event):
    await _guard(pet_feed_matcher)
    player_id = int(event.get_user_id())
    pet = await _load_pet(pet_feed_matcher, player_id)

    try:
        # 喂食不再区分食物：饥饿恢复固定值（interactions.yaml effects.feed）。
        result = await interaction.feed(pet)
    except ValueError as exc:
        await pet_feed_matcher.finish(f"❌ {exc}")

    hint = await _tutorial_prompt(pet, "first_feed")
    await pet_core.save_pet(pet)
    await pet_feed_matcher.finish(
        _compose(text("feed.ok", result["message"], **result), hint)
    )


@pet_play_matcher.handle()
async def handle_play(bot: Bot, event: Event):
    await _guard(pet_play_matcher)
    player_id = int(event.get_user_id())
    pet = await _load_pet(pet_play_matcher, player_id)

    try:
        result = await interaction.play(pet)
    except ValueError as exc:
        await pet_play_matcher.finish(f"❌ {exc}")

    hint = await _tutorial_prompt(pet, "first_play")
    await pet_core.save_pet(pet)
    await pet_play_matcher.finish(
        _compose(text("play.ok", result["message"], **result), hint)
    )


@pet_bathe_matcher.handle()
async def handle_bathe(bot: Bot, event: Event):
    await _guard(pet_bathe_matcher)
    player_id = int(event.get_user_id())
    pet = await _load_pet(pet_bathe_matcher, player_id)

    try:
        result = await interaction.bathe(pet)
    except ValueError as exc:
        await pet_bathe_matcher.finish(f"❌ {exc}")

    await pet_core.save_pet(pet)
    await pet_bathe_matcher.finish(text("bathe.ok", result["message"], **result))


@pet_rest_matcher.handle()
async def handle_rest(bot: Bot, event: Event):
    await _guard(pet_rest_matcher)
    player_id = int(event.get_user_id())
    pet = await _load_pet(pet_rest_matcher, player_id)

    try:
        result = await interaction.rest(pet)
    except ValueError as exc:
        await pet_rest_matcher.finish(f"❌ {exc}")

    await pet_core.save_pet(pet)
    await pet_rest_matcher.finish(text("rest.ok", result["message"], **result))


@pet_skill_matcher.handle()
async def handle_skill(bot: Bot, event: Event, args: Message = CommandArg()):
    """``/宠物.技能`` 列表；``装备|卸下 <技能名或ID>`` 调整装备槽。"""
    await _guard(pet_skill_matcher)
    player_id = int(event.get_user_id())
    pet = await _load_pet(pet_skill_matcher, player_id)

    raw = lk_util.get_trans_text(args).strip()
    if not raw:
        await pet_skill_matcher.finish(format_skill_list(pet))

    action, _, rest = raw.partition(" ")
    rest = rest.strip()
    if action not in ("装备", "卸下") or not rest:
        await pet_skill_matcher.finish(
            text(
                "skill.usage",
                "用法：/宠物.技能 装备 <技能名>｜/宠物.技能 卸下 <技能名>",
            )
        )

    try:
        skill_id = skills.resolve_skill(rest)
        if action == "装备":
            skills.equip_skill(pet, skill_id)
        else:
            skills.unequip_skill(pet, skill_id)
    except ValueError as exc:
        await pet_skill_matcher.finish(text("skill.unknown", "❌ {msg}", msg=str(exc)))

    await pet_core.save_pet(pet)
    key = "skill.equipped_ok" if action == "装备" else "skill.unequipped_ok"
    default = "✅ 已装备「{name}」" if action == "装备" else "✅ 已卸下「{name}」（仍保留在技能库中）"
    await pet_skill_matcher.finish(
        _compose(
            text(key, default, name=skills.skill_name(skill_id)),
            format_skill_list(pet),
        )
    )


@pet_talent_matcher.handle()
async def handle_talent(bot: Bot, event: Event, args: Message = CommandArg()):
    await _guard(pet_talent_matcher)
    player_id = int(event.get_user_id())
    pet = await _load_pet(pet_talent_matcher, player_id)

    talent_id = args.extract_plain_text().strip()
    if not talent_id:
        from ..config_loader import load_config

        cfg = load_config("talents")
        await pet_talent_matcher.finish(
            _compose(
                format_talent_list(pet, cfg),
                text(
                    "talent.usage",
                    "用法：/宠物.天赋 <天赋名或ID>",
                    talents=" / ".join(talents.describe_talents(cfg)),
                ),
            )
        )

    try:
        # 与性格同惯例：展示用中文名，输入接受中文名或英文键，落库存键。
        talent_id = talents.resolve_talent(talent_id)
        # allocate_talent 是同步函数（返回 dict），不能 await——await dict 抛 TypeError。
        result = talents.allocate_talent(pet, talent_id)
    except ValueError as exc:
        await pet_talent_matcher.finish(f"❌ {exc}")

    await pet_core.save_pet(pet)
    await pet_talent_matcher.finish(
        text(
            "talent.allocated",
            "{name} 提升到 Lv.{new_level}，剩余 {remaining_points} 点",
            **result,
        )
    )


@pet_bio_matcher.handle()
async def handle_bio(bot: Bot, event: Event):
    await _guard(pet_bio_matcher)
    player_id = int(event.get_user_id())
    pet = await _load_pet(pet_bio_matcher, player_id)

    bio = await biography.get_or_create_biography(player_id)
    await pet_bio_matcher.finish(format_biography(bio, name=pet.name))


@pet_log_matcher.handle()
async def handle_log(bot: Bot, event: Event):
    await _guard(pet_log_matcher)
    player_id = int(event.get_user_id())
    await _load_pet(pet_log_matcher, player_id)

    bio = await biography.get_or_create_biography(player_id)
    await pet_log_matcher.finish(format_daily_logs(bio))


@pet_reincarnate_matcher.handle()
async def handle_reincarnate(bot: Bot, event: Event, args: Message = CommandArg()):
    await _guard(pet_reincarnate_matcher)
    player_id = int(event.get_user_id())
    pet = await _load_pet(pet_reincarnate_matcher, player_id)

    check = await reincarnation.check_reincarnation_available(pet)
    reinc = await reincarnation.get_or_create_reincarnation(player_id)
    if not check["can_reincarnate"]:
        await pet_reincarnate_matcher.finish(format_reincarnation_check(check, reinc))

    if args.extract_plain_text().strip() != CONFIRM_WORD:
        await pet_reincarnate_matcher.finish(format_reincarnation_check(check, reinc))

    try:
        result = await reincarnation.perform_reincarnation(
            pet,
            player_id,
            manual=True,
            biography_hook=biography.add_biography_entry,
        )
    except ValueError as exc:
        await pet_reincarnate_matcher.finish(f"❌ {exc}")

    await pet_core.save_pet(pet)
    await pet_reincarnate_matcher.finish(format_reincarnation_result(result))


@pet_perks_matcher.handle()
async def handle_perks(bot: Bot, event: Event, args: Message = CommandArg()):
    await _guard(pet_perks_matcher)
    player_id = int(event.get_user_id())

    reinc = await reincarnation.get_or_create_reincarnation(player_id)
    available = reinc.achievement_points - reinc.spent_points

    perk_id = args.extract_plain_text().strip()
    if not perk_id:
        from ..config_loader import load_config

        await pet_perks_matcher.finish(
            format_perk_list(load_config("reincarnation"), available)
        )

    try:
        result = await reincarnation.spend_achievement_points(player_id, perk_id)
    except ValueError as exc:
        await pet_perks_matcher.finish(f"❌ {exc}")

    await pet_perks_matcher.finish(text("perk.granted", "", **result))


# ---------------------------------------------------------------------------
# 以下指令由旧插件合并进来：功能保留，数据源改为 database.py 的 PETDATA，
# 因此不再依赖已经删除的 pet_data.PetManager 内存缓存。
# ---------------------------------------------------------------------------


@pet_chat_matcher.handle(
    [
        Cooldown(10, prompt=_cooldown_notice()),
        Depends(is_chat_switch_on),
        Depends(not_safe_mode),
        Depends(is_lk_user),
    ]
)
async def handle_chat(event: Event, args: Message = CommandArg()):
    """``/宠物.聊天 <内容>`` — talk to the pet through the LLM."""
    await _guard(pet_chat_matcher)
    player_id = int(event.get_user_id())
    pet = await _load_pet(pet_chat_matcher, player_id)

    message = lk_util.get_trans_text(args).strip()
    if not message:
        await pet_chat_matcher.finish(
            text("chat.empty_message", "想对宠物说点什么呢？")
        )

    chat = chat_manager.get(pet, lk_util.get_name(str(player_id)))
    try:
        response = await chat.chat_with(f"{chat.user_name}:{message}")
    except Exception as exc:  # 聊天失败不该打断机器人
        log.warning(exc)
        await pet_chat_matcher.finish(text("chat.failed", "很可惜，宠物不理你了"))
        return

    log.info(response)
    reply = f"{pet.name}:\n{response}"
    if len(reply) > 100:
        await pet_chat_matcher.finish(img_msg(await text_to_pic(reply)))
    await pet_chat_matcher.finish(reply)


@pet_love_matcher.handle([Cooldown(30, prompt=_cooldown_notice())])
async def handle_love(event: Event):
    """``/宠物.好感度`` — merged from the legacy 好感度查询, now reading ``affection``."""
    await _guard(pet_love_matcher)
    player_id = int(event.get_user_id())
    pet = await _load_pet(pet_love_matcher, player_id)

    await pet_love_matcher.finish(
        text(
            "love.line",
            "",
            name=pet.name,
            affection=pet.affection,
            level=get_affection_level(pet.affection),
        )
    )


@pet_rename_matcher.handle([Cooldown(300, prompt=_cooldown_notice())])
async def handle_rename(event: Event, args: Message = CommandArg()):
    """``/宠物.改名`` — merged from the legacy 改名."""
    await _guard(pet_rename_matcher)
    player_id = int(event.get_user_id())
    pet = await _load_pet(pet_rename_matcher, player_id)

    raw = args.extract_plain_text().strip()
    if not raw:
        await pet_rename_matcher.finish(
            text("rename.usage", "用法：/宠物.改名 <新名字>")
        )

    try:
        new_name = pet_core.clean_and_validate_name(raw)
    except ValueError as exc:
        await pet_rename_matcher.finish(f"❌ {exc}")

    old_name, new_name = await pet_core.rename_pet(pet, new_name)
    await pet_rename_matcher.finish(text("rename.ok", "", old=old_name, new=new_name))


@pet_instruction_matcher.handle([Cooldown(300, prompt=_cooldown_notice())])
async def handle_instruction(event: Event, args: Message = CommandArg()):
    """``/宠物.设定`` — merged from the legacy 更改设定; feeds the chat persona."""
    await _guard(pet_instruction_matcher)
    player_id = int(event.get_user_id())
    pet = await _load_pet(pet_instruction_matcher, player_id)

    instruction = pet_core.clean_instruction(args.extract_plain_text())
    if not instruction:
        await pet_instruction_matcher.finish(
            text("instruction.usage", "用法：/宠物.设定 <设定内容>")
        )

    await pet_core.set_instruction(pet, instruction)
    await pet_instruction_matcher.finish(text("instruction.ok", "设定更新成功啦"))


async def _send_paged(matcher, title: str, entries: list[tuple[str, str]]) -> None:
    """Send ``(user_id, name)`` entries in pages of :data:`PAGE_SIZE`."""
    total = len(entries)
    if total == 0:
        await matcher.finish(text("petlist.empty", "还没有任何宠物"))

    for start in range(0, total, PAGE_SIZE):
        chunk = entries[start : start + PAGE_SIZE]
        lines = [title]
        for offset, (user_id, name) in enumerate(chunk, start=start + 1):
            lines.append(
                text(
                    "petlist.line",
                    "",
                    index=offset,
                    name=name,
                    user_id=user_id,
                )
            )
        lines.append(
            text(
                "petlist.total",
                "",
                count=min(start + PAGE_SIZE, total),
                total=total,
            )
        )
        await matcher.send("\n".join(lines))


@pet_group_list_matcher.handle()
async def handle_group_list(bot: Bot, event: Event):
    """``/宠物.列表`` (ADMIN) — names come straight from the database."""
    await _guard(pet_group_list_matcher)
    group_id = getattr(event, "group_id", None)
    if group_id is None:
        await pet_group_list_matcher.finish(
            text("petlist.group_only", "这个指令只能在群里使用")
        )

    names = load_pet_names()
    entries = []
    for member in await bot.get_group_member_list(group_id=int(group_id)):
        user_id = str(member["user_id"])
        if user_id.isdigit() and int(user_id) in names:
            entries.append((user_id, names[int(user_id)]))

    await _send_paged(
        pet_group_list_matcher,
        text("petlist.group_title", "本群宠物列表:"),
        entries,
    )


@pet_all_list_matcher.handle()
async def handle_all_list(bot: Bot):
    """``/宠物.全部`` (MASTER) — walks every group the bot is in."""
    await _guard(pet_all_list_matcher)

    names = load_pet_names()
    entries: dict[str, str] = {}
    for group in await bot.get_group_list():
        for member in await bot.get_group_member_list(group_id=group["group_id"]):
            user_id = str(member["user_id"])
            if user_id not in entries and user_id.isdigit() and int(user_id) in names:
                entries[user_id] = names[int(user_id)]

    await _send_paged(
        pet_all_list_matcher,
        text("petlist.all_title", "所有宠物列表:"),
        list(entries.items()),
    )


# ---------------------------------------------------------------------------
# 附加玩法（任务书 §12 肉鸽）：动作为参数，编号为局内应答。
# ---------------------------------------------------------------------------


@pet_rogue_matcher.handle()
async def handle_rogue(event: Event, args: Message = CommandArg()):
    """``/宠物.肉鸽 开始|继续|进度|撤退``；局内回复编号推进。"""
    await _guard(pet_rogue_matcher)
    player_id = int(event.get_user_id())
    pet = await _load_pet(pet_rogue_matcher, player_id)
    # 跨日进入时先结算，行动点重置后入场/前进才判得准。
    await _settle(pet_rogue_matcher, pet, player_id)

    raw = args.extract_plain_text().strip()
    rogue = await roguelike.get_or_create_rogue(player_id)
    try:
        if raw == "":
            usage = _never_empty(text("rogue.usage", ""), _ROGUE_USAGE_HINT)
            msg = roguelike.progress(rogue) + "\n" + usage
        elif raw == "开始":
            msg = await roguelike.start(pet, rogue)
        elif raw == "继续":
            msg = await roguelike.resume(pet, rogue)
        elif raw == "进度":
            msg = roguelike.progress(rogue)
        elif raw == "撤退":
            msg = await roguelike.retreat(pet, rogue)
        elif raw.isdigit():
            msg = await roguelike.handle_number(pet, rogue, int(raw))
        else:
            msg = _never_empty(text("rogue.usage", ""), _ROGUE_USAGE_HINT)
    except ValueError as exc:
        await pet_rogue_matcher.finish(f"❌ {exc}")
    except Exception as exc:  # 未知错误也要给用户回执，不静默吞掉对话
        log.warning(f"宠物.肉鸽 处理失败：{exc!r}")
        await pet_rogue_matcher.finish("❌ 肉鸽指令执行出错，请查看机器人日志。")

    await pet_core.save_pet(pet)
    await pet_rogue_matcher.finish(_never_empty(msg, _ROGUE_USAGE_HINT))


@pet_train_matcher.handle()
async def handle_train(bot: Bot, event: Event):
    await _guard(pet_train_matcher)
    player_id = int(event.get_user_id())
    pet = await _load_pet(pet_train_matcher, player_id)
    await _settle(pet_train_matcher, pet, player_id)

    try:
        result = await interaction.train(pet, player_id)
    except ValueError as exc:
        await pet_train_matcher.finish(f"❌ {exc}")

    await pet_core.save_pet(pet)
    await pet_train_matcher.finish(text("train.ok", result["message"], **result))
