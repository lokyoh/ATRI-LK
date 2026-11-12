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

from nonebot.adapters import Bot, Event
from nonebot.adapters import Message as BaseMessage
from nonebot.adapters.onebot.v11 import Bot as OneBot11
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
from ATRI.system.lkapi.entity.user import user_manager

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
from ..core.affection import (
    AFFECTION_MAX,
    affection_bonus_ratio,
    get_affection_level,
)
from ..core.chat import chat_manager
from ..database import load_pet_names
from ..matchers import (
    pet_all_list_matcher,
    pet_bathe_matcher,
    pet_bio_matcher,
    pet_chat_matcher,
    pet_create_matcher,
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
    pet_shop_matcher,
    pet_skill_matcher,
    pet_status_matcher,
    pet_talent_matcher,
    pet_train_matcher,
)
from ..utils import menus, reply, rogue_actions, style
from ..utils.cooldown import cooldown
from ..utils.formatters import (
    format_action_result,
    format_biography,
    format_daily_logs,
    format_daily_report,
    format_pet_status,
    format_reincarnation_check,
    format_reincarnation_result,
    format_shop_category,
    format_shop_item,
    format_shop_overview,
    format_shop_purchase,
    format_shop_respec,
    format_skill_list,
    format_talent_list,
)
from ..utils.texts import apply_command_start, text, text_list

CONFIRM_WORD = "确认"
"""``/宠物.轮回 确认`` is the only way to end a cycle."""

PAGE_SIZE = 20
"""Entries per message for the two pet-listing commands."""

_COOLDOWN_NOTICES = text_list("cooldown.notices", ["冷静一下"])
"""Prompts shown when a command is used faster than its cooldown allows.

**每次触发时随机取一句**——见 ``utils/cooldown.py``。不要在这里预先
``choice()`` 出一个字符串交给装饰器，那会把选择固定在模块导入那一刻。
"""

_ROGUE_USAGE_HINT = "用法：/宠物.肉鸽 开始｜继续｜进度｜撤退"


def _rogue_usage() -> str:
    """肉鸽的用法提示。

    每个动作都做成**可点命令**（QQ 端）：这段文案出现的场合正是「用户不知道
    下一步该输什么」，把指令写成纯文本等于让他照着抄一遍。

    「回复编号应答」只在 OneBot 端成立——QQ 端每层都会直接给可点的选项，
    用户不用记编号。
    """

    parts = [
        menus.inline(label, f"宠物.肉鸽 {action}")
        for label, action in (
            ("开始", "开始"),
            ("继续", "继续"),
            ("进度", "进度"),
            ("撤退", "撤退"),
        )
    ]
    parts = [p for p in parts if p]
    if parts:
        return "用法（点一下即可）：\n> " + " ".join(parts)
    return _ROGUE_USAGE_HINT + "\n进入层数后按提示回复编号应答。"


def _player_id(event) -> str:
    """取玩家 ID：先按适配器取原始 ID，再交给 lkbot 解析绑定。

    两件事都必须做对：

    1. **不能 int()**：OneBot 的 QQ 号是数字，但 QQ 官方适配器的
       ``get_user_id()`` 返回 ``user_openid``（``ROBOT1.0_Xb5...``），转 int
       直接 ValueError——那正是「宠物.状态 在 QQ 端报错」的原因。
    2. **要过 ``resolve_user_id``**：一个玩家可能同时用 QQ 与 OneBot 两个账号，
       lkbot 会把它们绑定到同一个主账号。绕过解析意味着同一个人在两端的宠物
       数据各存一份——用户看到的是「换了个平台宠物就没了」。

    解析失败（绑定关系损坏等）时退回原始 ID：拿得到数据比拿不到好。
    """
    raw = str(event.get_user_id())
    try:
        return user_manager.resolve_user_id(raw)
    except Exception as exc:
        log.warning(f"解析玩家 {raw} 的绑定账号失败，按原 ID 处理：{exc}")
        return raw


def _text_of(args) -> str:
    """把命令参数取成纯文本，跨适配器安全。

    ``lk_util.get_trans_text`` 是按 OneBot 的段结构写的——它在 ``at`` 段上直接
    取 ``data["qq"]``，而 QQ 官方适配器的 at 段没有这个键，会 KeyError。
    所以 QQ 端一律走 ``extract_plain_text``，OneBot 端才用原来的富文本转换
    （它能把 @、表情、图片摘要一并转成文字）。
    """
    if isinstance(args, str):
        return args.strip()
    try:
        from nonebot.adapters.qq import Message as QQMsg

        if isinstance(args, QQMsg):
            return args.extract_plain_text().strip()
    except Exception:
        pass
    try:
        return lk_util.get_trans_text(args).strip()
    except (KeyError, AttributeError, TypeError):
        # 段结构和 OneBot 不一样时的兜底：能取多少算多少
        try:
            return args.extract_plain_text().strip()
        except Exception:
            return ""


def _never_empty(message: str, fallback: str) -> str:
    """防空回复：texts.yaml 缺键（部署未同步 res/ 配置）时 ``text(key, "")`` 会得到
    空串，QQ 端把空消息显示为「该消息类型暂不支持查看」。空串一律降级为兜底
    文案，绝不发空消息。

    兜底文案里可能有已经生成好的 ``<qqbot-cmd-input>`` 标记——那种走进来就
    不要再 ``apply_command_start``：它按字面替换 ``/宠物``，会把标记里的
    URL 编码内容一起改坏。
    """
    if message and message.strip():
        return message
    if reply.QQ_CMD_MARK in fallback:
        return fallback
    return apply_command_start(fallback)


def _cooldown_notice() -> str:
    """保留的兼容入口：返回**全部**候选里随机的一句。

    真正的冷却依赖在 ``utils/cooldown.py``，它在触发时才随机取值。这个函数
    只给仍按旧写法调用的人用，避免再次出现「装饰器求值时就把句子定死」。
    """
    return choice(_COOLDOWN_NOTICES) if _COOLDOWN_NOTICES else "冷静一下"


async def _guard(matcher) -> None:
    """Refuse every command while the raising system is switched off."""
    if not lkpet_config.get("enable"):
        await reply.finish(matcher, text("status.disabled", "养成系统当前已关闭。"))


async def _load_pet(matcher, player_id: str):
    """Return the caller's pet, or finish with a hint when they have none."""
    pet = await pet_core.get_pet(player_id)
    if pet is None:
        await reply.finish(
            matcher,
            text("common.no_pet", "请先创建宠物：/宠物.创建 <性格> <名字>")
            + _create_shortcut(),
        )
    return pet


def _create_shortcut() -> str:
    """「还没有宠物」时的创建入口（仅 QQ 端有内容）。

    这里给的是**每个性格一个按钮**，点下去等于把 ``/宠物.创建 勇敢`` 插进输入框
    （``<qqbot-cmd-input>`` 的语义就是「插入、由用户确认发送」），用户只需接着
    打名字就能发出。之前是一个只填了一半的 ``/宠物.创建 `` 空命令，点完还是一样
    要自己补性格——那等于没省事。

    指令末尾刻意留一个空格：光标会落在参数区，接着敲名字即可。
    """
    from ..utils import reply

    if not reply.is_qq():
        return ""
    try:
        personalities = pet_core.describe_personalities()
    except Exception:  # 配置缺失时退回不带性格的通用入口
        personalities = []
    if not personalities:
        return "\n> " + reply.cmd_at("点这里填名字与性格", menus.cmd("宠物.创建 "))
    choices = [
        reply.choice(name, menus.cmd(f"宠物.创建 {name} ")) for name in personalities
    ]
    # 结尾用「引用块」形状（`> ` 开头）而不是裸标记：QQ 端按钮不会把整条消息
    # 拖到纯文本样式，OneBot 端这几行在 formatters 之外也不会被误当成指令。
    return "\n> 选一个性格（点一下填入输入框，再补名字）：\n> " + reply.inline_links(
        choices
    ).replace("\n", "\n> ")


async def _settle(matcher, pet, player_id: str) -> dict:
    """Run the day settlement with biography and reincarnation wired in."""
    result = await daily.check_daily_reset(
        pet,
        daily_log_writer=biography.add_daily_log,
        reincarnation_checker=reincarnation.check_reincarnation_available,
    )
    if result.get("need_settle"):
        await pet_core.save_pet(pet)
    return result


async def _settle_before_action(matcher, pet, player_id: str) -> None:
    """行动类指令的统一前置：**先结算过期天数**，再判断行动点。

    没有这一步时，跨天后的第一个行动指令读到的仍是昨天的 ``action_points``
    （技能/背包都还是旧的）——玩家会看到「行动点不足」或莫名多扣一天的量。
    结算明细不在这里播报：行动回执里已经带有当前行动点，
    完整的日结算卡片由「/宠物.状态」与定时推送负责（没有手动结算指令）。
    """
    try:
        await _settle(matcher, pet, player_id)
    except Exception as exc:  # 结算失败不该让玩家连喂食都做不了
        log.warning(f"lkpet 日结算失败（{matcher}）：{exc!r}")


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
async def handle_create(event: Event, args: BaseMessage = CommandArg()):
    await _guard(pet_create_matcher)
    player_id = _player_id(event)

    parts = args.extract_plain_text().strip().split()
    if len(parts) < 2:
        await reply.finish(
            pet_create_matcher,
            text(
                "create.usage",
                "用法：/宠物.创建 <性格> <名字> [设定]",
                personalities=" / ".join(pet_core.describe_personalities()),
            ),
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
        await reply.finish(pet_create_matcher, f"❌ {exc}")

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
    await reply.finish(
        pet_create_matcher,
        _compose(
            text("create.ok", "🎉 宠物创建成功！", **create_fields),
            await _tutorial_prompt(pet, "welcome"),
        ),
        menus.care_menu(),
    )


@pet_status_matcher.handle()
async def handle_status(event: Event):
    await _guard(pet_status_matcher)
    player_id = _player_id(event)
    pet = await _load_pet(pet_status_matcher, player_id)

    settle = await _settle(pet_status_matcher, pet, player_id)
    hint = await _tutorial_prompt(pet, "first_status")
    await pet_core.save_pet(pet)

    report = format_daily_report(settle) if settle.get("need_settle") else None
    await reply.finish(
        pet_status_matcher,
        _compose(report, format_pet_status(pet), hint),
        menus.care_menu(),
    )


@pet_feed_matcher.handle()
async def handle_feed(event: Event):
    await _guard(pet_feed_matcher)
    player_id = _player_id(event)
    pet = await _load_pet(pet_feed_matcher, player_id)
    await _settle_before_action(pet_feed_matcher, pet, player_id)

    try:
        # 喂食不再区分食物：饱食恢复固定值（interactions.yaml effects.feed）。
        result = await interaction.feed(pet)
    except ValueError as exc:
        await reply.finish(pet_feed_matcher, f"❌ {exc}")

    hint = await _tutorial_prompt(pet, "first_feed")
    await pet_core.save_pet(pet)
    await reply.finish(
        pet_feed_matcher,
        _compose(format_action_result("feed", result), hint),
        menus.action_menu(),
    )


@pet_play_matcher.handle()
async def handle_play(event: Event):
    await _guard(pet_play_matcher)
    player_id = _player_id(event)
    pet = await _load_pet(pet_play_matcher, player_id)
    await _settle_before_action(pet_play_matcher, pet, player_id)

    try:
        result = await interaction.play(pet)
    except ValueError as exc:
        await reply.finish(pet_play_matcher, f"❌ {exc}")

    hint = await _tutorial_prompt(pet, "first_play")
    await pet_core.save_pet(pet)
    await reply.finish(
        pet_play_matcher,
        _compose(format_action_result("play", result), hint),
        menus.action_menu(),
    )


@pet_bathe_matcher.handle()
async def handle_bathe(event: Event):
    await _guard(pet_bathe_matcher)
    player_id = _player_id(event)
    pet = await _load_pet(pet_bathe_matcher, player_id)
    await _settle_before_action(pet_bathe_matcher, pet, player_id)

    try:
        result = await interaction.bathe(pet)
    except ValueError as exc:
        await reply.finish(pet_bathe_matcher, f"❌ {exc}")

    await pet_core.save_pet(pet)
    await reply.finish(
        pet_bathe_matcher,
        format_action_result("bathe", result),
        menus.action_menu(),
    )


@pet_rest_matcher.handle()
async def handle_rest(event: Event):
    await _guard(pet_rest_matcher)
    player_id = _player_id(event)
    pet = await _load_pet(pet_rest_matcher, player_id)
    await _settle_before_action(pet_rest_matcher, pet, player_id)

    try:
        result = await interaction.rest(pet)
    except ValueError as exc:
        await reply.finish(pet_rest_matcher, f"❌ {exc}")

    await pet_core.save_pet(pet)
    await reply.finish(
        pet_rest_matcher,
        format_action_result("rest", result),
        menus.action_menu(),
    )


@pet_skill_matcher.handle()
async def handle_skill(event: Event, args: BaseMessage = CommandArg()):
    """``/宠物.技能`` 列表；``装备|卸下 <技能名或ID>`` 调整装备槽。"""
    await _guard(pet_skill_matcher)
    player_id = _player_id(event)
    pet = await _load_pet(pet_skill_matcher, player_id)

    raw = _text_of(args)
    if not raw:
        # 装备/卸下的命令已内嵌在每条技能行末，底部只放导航
        await reply.finish(
            pet_skill_matcher, format_skill_list(pet), menus.skill_menu()
        )

    action, _, rest = raw.partition(" ")
    rest = rest.strip()
    if action not in ("装备", "卸下") or not rest:
        await reply.finish(
            pet_skill_matcher,
            text(
                "skill.usage",
                "用法：/宠物.技能 装备 <技能名>｜/宠物.技能 卸下 <技能名>",
            ),
        )

    try:
        skill_id = skills.resolve_skill(rest)
        if action == "装备":
            skills.equip_skill(pet, skill_id)
        else:
            skills.unequip_skill(pet, skill_id)
    except ValueError as exc:
        await reply.finish(
            pet_skill_matcher, text("skill.unknown", "❌ {msg}", msg=str(exc))
        )

    await pet_core.save_pet(pet)
    key = "skill.equipped_ok" if action == "装备" else "skill.unequipped_ok"
    default = (
        "✅ 已装备「{name}」"
        if action == "装备"
        else "✅ 已卸下「{name}」（仍保留在技能库中）"
    )
    await reply.finish(
        pet_skill_matcher,
        _compose(
            text(key, default, name=skills.skill_name(skill_id)),
            format_skill_list(pet),
        ),
        menus.skill_menu(),
    )


@pet_talent_matcher.handle()
async def handle_talent(event: Event, args: BaseMessage = CommandArg()):
    await _guard(pet_talent_matcher)
    player_id = _player_id(event)
    pet = await _load_pet(pet_talent_matcher, player_id)

    talent_id = args.extract_plain_text().strip()
    from ..config_loader import load_config

    cfg = load_config("talents")
    if not talent_id:
        await reply.finish(
            pet_talent_matcher, format_talent_list(pet, cfg), menus.talent_menu()
        )

    try:
        # 与性格同惯例：展示用中文名，输入接受中文名或英文键，落库存键。
        talent_id = talents.resolve_talent(talent_id)
        # allocate_talent 是同步函数（返回 dict），不能 await——await dict 抛 TypeError。
        result = talents.allocate_talent(pet, talent_id)
    except ValueError as exc:
        await reply.finish(pet_talent_matcher, f"❌ {exc}")

    await pet_core.save_pet(pet)
    # 加完一点立刻重画整张卡：玩家下一步通常还是加点，让最新等级与剩下的
    # 「加一点」按钮直接摆在眼前，省掉再发一次 /宠物.天赋。
    await reply.finish(
        pet_talent_matcher,
        _compose(
            text(
                "talent.allocated",
                "{name} 提升到 Lv.{new_level}，剩余 {remaining_points} 点",
                **result,
            ),
            format_talent_list(pet, cfg),
        ),
        menus.talent_menu(),
    )


@pet_bio_matcher.handle()
async def handle_bio(event: Event):
    await _guard(pet_bio_matcher)
    player_id = _player_id(event)
    pet = await _load_pet(pet_bio_matcher, player_id)

    bio = await biography.get_or_create_biography(player_id)
    await reply.finish(
        pet_bio_matcher, format_biography(bio, name=pet.name), menus.bio_menu()
    )


@pet_log_matcher.handle()
async def handle_log(event: Event):
    await _guard(pet_log_matcher)
    player_id = _player_id(event)
    await _load_pet(pet_log_matcher, player_id)

    bio = await biography.get_or_create_biography(player_id)
    await reply.finish(pet_log_matcher, format_daily_logs(bio), menus.log_menu())


@pet_reincarnate_matcher.handle()
async def handle_reincarnate(event: Event, args: BaseMessage = CommandArg()):
    await _guard(pet_reincarnate_matcher)
    player_id = _player_id(event)
    pet = await _load_pet(pet_reincarnate_matcher, player_id)

    check = await reincarnation.check_reincarnation_available(pet)
    reinc = await reincarnation.get_or_create_reincarnation(player_id)
    if not check["can_reincarnate"]:
        await reply.finish(
            pet_reincarnate_matcher,
            format_reincarnation_check(check, reinc),
            menus.reincarnation_menu(ready=False),
        )

    if args.extract_plain_text().strip() != CONFIRM_WORD:
        # 条件够了但没写「确认」：给个按钮，省得手打
        await reply.finish(
            pet_reincarnate_matcher,
            format_reincarnation_check(check, reinc),
            menus.reincarnation_menu(ready=True),
        )

    try:
        result = await reincarnation.perform_reincarnation(
            pet,
            player_id,
            manual=True,
            biography_hook=biography.add_biography_entry,
        )
    except ValueError as exc:
        await reply.finish(pet_reincarnate_matcher, f"❌ {exc}")

    await pet_core.save_pet(pet)
    # 轮回完成 = 新的一世刚开局，最该做的是照护与成长，不是配技能/逛商店。
    await reply.finish(
        pet_reincarnate_matcher,
        format_reincarnation_result(result),
        menus.care_menu(),
    )


# ---------------------------------------------------------------------------
# 轮回商店
#
# 参数语法（全部按空格切，与 /宠物.技能 一致）：
#   （空）            概览
#   1                 分类 1
#   1-3               商品详情
#   确认 1-3          购买
#   洗点 / 洗点 确认   洗点预览 / 执行
# ---------------------------------------------------------------------------


async def _shop_dispatch(matcher, event: Event, args: BaseMessage) -> None:
    """``/宠物.商店`` 与 ``/宠物.特权`` 共用的实现。

    所有分支都以 ``matcher.finish`` 结束，因此本函数不会正常返回。
    """
    from ..core import shop

    player_id = _player_id(event)
    reinc = await reincarnation.get_or_create_reincarnation(player_id)
    raw = args.extract_plain_text().strip()

    # 概览
    if not raw:
        await reply.finish(
            matcher,
            format_shop_overview(reinc),
            menus.shop_overview_menu(_shop_categories()),
        )

    # 洗点：``洗点`` 预览 / ``洗点 确认`` 执行
    if raw.split()[0] == "洗点":
        rest = raw.split()[1:]
        try:
            if rest and rest[0] == CONFIRM_WORD:
                result = shop.respec(reinc)
                await _save_reinc(reinc)
                pet = await pet_core.get_pet(player_id)
                if pet is not None:
                    reincarnation.refresh_pet_attributes(pet, reinc.permanent_bonuses)
                    await pet_core.save_pet(pet)
                await reply.finish(matcher, format_shop_respec(result, done=True))
            preview = shop.respec_refund(reinc)
            if preview["tickets"] <= 0:
                await reply.finish(
                    matcher, text("shop.respec_no_ticket", "没有洗点券了。")
                )
            if not preview["cleared"]:
                await reply.finish(
                    matcher, text("shop.respec_none", "现在没有任何可洗的商品。")
                )
            await reply.finish(matcher, format_shop_respec(preview))
        except ValueError as exc:
            await reply.finish(matcher, text("shop.unknown", "❌ {msg}", msg=str(exc)))

    parts = raw.split()
    # 购买：``确认 1-3``
    if parts[0] == CONFIRM_WORD:
        if len(parts) < 2:
            await reply.finish(
                matcher,
                text("shop.unknown", "❌ {msg}", msg="用法：/宠物.商店 确认 1-3"),
            )
        await _shop_buy(matcher, player_id, reinc, parts[1])

    token = parts[0]
    # 分类：``1``
    if token.isdigit():
        cat = next(
            (c for c in shop.categories() if int(c.get("order", 999)) == int(token)),
            None,
        )
        if cat is None:
            await reply.finish(
                matcher, text("shop.unknown", "❌ {msg}", msg=f"没有分类 {token}")
            )
        # 「查看」已内嵌在每件商品行末，底部不再重复
        await reply.finish(matcher, format_shop_category(reinc, str(cat.get("id"))))

    # 商品详情或直接购买：``1-3`` / ``legacy_atk`` / ``前世武艺``
    try:
        item_id = shop.resolve_item(token)
    except ValueError as exc:
        await reply.finish(matcher, text("shop.unknown", "❌ {msg}", msg=str(exc)))

    spec = shop.catalog().get(item_id) or {}
    price = shop.next_price(reinc, spec)
    # 便宜的东西免二次确认（阈值见 shop.confirm_threshold），贵的要先看详情。
    if price is not None and price <= _shop_confirm_threshold():
        await _shop_buy(matcher, player_id, reinc, item_id)
    await reply.finish(
        matcher,
        format_shop_item(reinc, item_id),
        menus.shop_item_menu(_shop_number(token), affordable=price is not None),
    )


def _shop_categories() -> list[dict]:
    """商店分类（渲染按钮用）；取不到就返回空列表，不影响正文。"""
    try:
        from ..core import shop

        return list(shop.categories())
    except Exception:
        return []


def _shop_number(token: str) -> str:
    """商品的展示编号（形如 ``1-3``）。

    **不能转 int**：编号是 ``分类-序号`` 的字符串，``item_number`` 也返回 str；
    转 int 会失败或丢掉后半段，按钮就会指向另一件商品——买错东西比按不动严重。
    """
    try:
        from ..core import shop

        return str(shop.item_number(token))
    except Exception:
        return str(token)


def _shop_confirm_threshold() -> int:
    """低于此价格免二次确认；读配置，缺省 200。"""
    from ..config_loader import load_config

    node = load_config("reincarnation").get("shop") or {}
    try:
        return int(node.get("confirm_threshold", 200) or 0)
    except (TypeError, ValueError):
        return 200


async def _save_reinc(reinc) -> None:
    """保存轮回行（商店的所有写操作最终都落到这里）。"""
    from datetime import datetime

    from ..database import save_reincarnation

    reinc.updated_at = datetime.now()
    save_reincarnation(reinc)


async def _shop_buy(matcher, player_id: str, reinc, token: str) -> None:
    """购买一件商品并立即把加成落到宠物属性上。"""
    from ..core import shop

    try:
        item_id = shop.resolve_item(token)
        spec = shop.catalog().get(item_id) or {}
        result = shop.purchase(reinc, item_id)
    except ValueError as exc:
        await reply.finish(matcher, text("shop.unknown", "❌ {msg}", msg=str(exc)))

    result["consumable_on_reincarnation"] = bool(
        result.get("consumed_on_reincarnation")
    ) or shop.consumes_on_reincarnation(spec)
    await _save_reinc(reinc)

    # 买完立刻重算属性：否则玩家要等到下次升级才看得见加成。
    pet = await pet_core.get_pet(player_id)
    if pet is not None:
        reincarnation.refresh_pet_attributes(pet, reinc.permanent_bonuses)
        await pet_core.save_pet(pet)

    await reply.finish(matcher, format_shop_purchase(result))


@pet_shop_matcher.handle()
async def handle_shop(event: Event, args: BaseMessage = CommandArg()):
    """``/宠物.商店`` —— 概览 / 分类 / 详情 / 购买 / 洗点。"""
    await _guard(pet_shop_matcher)
    await _shop_dispatch(pet_shop_matcher, event, args)


@pet_perks_matcher.handle()
async def handle_perks(event: Event, args: BaseMessage = CommandArg()):
    """``/宠物.特权`` —— 旧指令，转发到商店（行为完全一致）。"""
    await _guard(pet_perks_matcher)
    await _shop_dispatch(pet_perks_matcher, event, args)


# ---------------------------------------------------------------------------
# 以下指令由旧插件合并进来：功能保留，数据源改为 database.py 的 PETDATA，
# 因此不再依赖已经删除的 pet_data.PetManager 内存缓存。
# ---------------------------------------------------------------------------


@pet_chat_matcher.handle(
    [
        cooldown(10, notices=_COOLDOWN_NOTICES),
        Depends(is_chat_switch_on),
        Depends(not_safe_mode),
        Depends(is_lk_user),
    ]
)
async def handle_chat(event: Event, args: BaseMessage = CommandArg()):
    """``/宠物.聊天 <内容>`` — talk to the pet through the LLM."""
    await _guard(pet_chat_matcher)
    player_id = _player_id(event)
    pet = await _load_pet(pet_chat_matcher, player_id)

    message = _text_of(args)
    if not message:
        await reply.finish(
            pet_chat_matcher, text("chat.empty_message", "想对宠物说点什么呢？")
        )

    chat = chat_manager.get(pet, lk_util.get_name(str(player_id)))
    try:
        response = await chat.chat_with(f"{chat.user_name}:{message}")
    except Exception as exc:  # 聊天失败不该打断机器人
        log.warning(exc)
        await reply.finish(
            pet_chat_matcher, text("chat.failed", "很可惜，宠物不理你了")
        )
        return

    log.info(response)
    # 变量名不能叫 reply——那是本文件导入的回复模块
    answer = f"{pet.name}:\n{response}"
    if len(answer) > 100:
        # QQ 端用 md：长文本本来就能排版，不必转图（转图反而看不到可点的按钮）
        if reply.is_qq(event):
            await reply.finish(pet_chat_matcher, answer, menus.care_menu())
        await reply.finish(
            pet_chat_matcher, img_msg(await text_to_pic(answer)), menus.care_menu()
        )
    await reply.finish(pet_chat_matcher, answer, menus.care_menu())


@pet_love_matcher.handle([cooldown(30, notices=_COOLDOWN_NOTICES)])
async def handle_love(event: Event):
    """``/宠物.好感度`` — merged from the legacy 好感度查询, now reading ``affection``."""
    await _guard(pet_love_matcher)
    player_id = _player_id(event)
    pet = await _load_pet(pet_love_matcher, player_id)

    # 好感同时加成经验与战斗输出（最多 +10%），这里把当前档位一并说明，
    # 免得玩家只看到数字却不知道它有什么用。
    bonus_pct = round(affection_bonus_ratio(pet.affection) * 100, 1)
    await reply.finish(
        pet_love_matcher,
        _compose(
            text("love.title", "💗 {name} 的好感度", name=pet.name),
            text(
                "love.line",
                "亲密 {bar} {affection}/{affection_max}（{level}）",
                bar=style.bar(pet.affection, AFFECTION_MAX),
                affection=pet.affection,
                affection_max=AFFECTION_MAX,
                level=get_affection_level(pet.affection),
            ),
            text("love.bonus", "", bonus_pct=f"{bonus_pct:g}"),
        ),
        menus.detail_menu(),
    )


@pet_rename_matcher.handle([cooldown(300, notices=_COOLDOWN_NOTICES)])
async def handle_rename(event: Event, args: BaseMessage = CommandArg()):
    """``/宠物.改名`` — merged from the legacy 改名."""
    await _guard(pet_rename_matcher)
    player_id = _player_id(event)
    pet = await _load_pet(pet_rename_matcher, player_id)

    raw = args.extract_plain_text().strip()
    if not raw:
        await reply.finish(
            pet_rename_matcher, text("rename.usage", "用法：/宠物.改名 <新名字>")
        )

    try:
        new_name = pet_core.clean_and_validate_name(raw)
    except ValueError as exc:
        await reply.finish(pet_rename_matcher, f"❌ {exc}")

    old_name, new_name = await pet_core.rename_pet(pet, new_name)
    await reply.finish(
        pet_rename_matcher, text("rename.ok", "", old=old_name, new=new_name)
    )


@pet_instruction_matcher.handle([cooldown(300, notices=_COOLDOWN_NOTICES)])
async def handle_instruction(event: Event, args: BaseMessage = CommandArg()):
    """``/宠物.设定`` — merged from the legacy 更改设定; feeds the chat persona."""
    await _guard(pet_instruction_matcher)
    player_id = _player_id(event)
    pet = await _load_pet(pet_instruction_matcher, player_id)

    instruction = pet_core.clean_instruction(args.extract_plain_text())
    if not instruction:
        await reply.finish(
            pet_instruction_matcher,
            text("instruction.usage", "用法：/宠物.设定 <设定内容>"),
        )

    await pet_core.set_instruction(pet, instruction)
    await reply.finish(
        pet_instruction_matcher, text("instruction.ok", "设定更新成功啦")
    )


async def _send_paged(matcher, title: str, entries: list[tuple[str, str]]) -> None:
    """Send ``(user_id, name)`` entries in pages of :data:`PAGE_SIZE`."""
    total = len(entries)
    if total == 0:
        await reply.finish(matcher, text("petlist.empty", "还没有任何宠物"))

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
        await reply.send(matcher, "\n".join(lines))


@pet_group_list_matcher.handle()
async def handle_group_list(bot: Bot, event: Event):
    """``/宠物.列表`` (ADMIN) — names come straight from the database.

    需要 ``get_group_member_list``（目前只有 OneBot 提供）。这里用适配器无关的
    ``Bot`` 声明、运行时判断能力：QQ 端会回一句「该功能暂不支持」，而不是被
    依赖注入**静默跳过**——后者用户看到的是「命令没反应」，很难查。
    """
    await _guard(pet_group_list_matcher)
    if not isinstance(bot, OneBot11):
        await reply.finish(
            pet_group_list_matcher,
            text(
                "petlist.unsupported",
                "本群宠物列表需要 OneBot 适配器，当前平台暂不支持。",
            ),
        )

    group_id = getattr(event, "group_id", None)
    if group_id is None:
        await reply.finish(
            pet_group_list_matcher, text("petlist.group_only", "这个指令只能在群里使用")
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
    """``/宠物.全部`` (MASTER) — walks every group the bot is in.

    同 ``handle_group_list``：需要 OneBot 的群列表接口，Runtime 判断后给出提示。
    """
    await _guard(pet_all_list_matcher)
    if not isinstance(bot, OneBot11):
        await reply.finish(
            pet_all_list_matcher,
            text(
                "petlist.unsupported",
                "所有宠物列表需要 OneBot 适配器，当前平台暂不支持。",
            ),
        )

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
async def handle_rogue(event: Event, args: BaseMessage = CommandArg()):
    """``/宠物.肉鸽 开始|继续|进度|撤退``；局内回复编号推进。"""
    await _guard(pet_rogue_matcher)
    player_id = _player_id(event)
    pet = await _load_pet(pet_rogue_matcher, player_id)
    # 跨日进入时先结算，行动点重置后入场/前进才判得准。
    await _settle(pet_rogue_matcher, pet, player_id)

    raw = args.extract_plain_text().strip()
    rogue = await roguelike.get_or_create_rogue(player_id)
    # QQ 端的选项由底部按钮承载，正文里再列一遍编号就是同一件事说两次；
    # OneBot 端没有按钮，编号必须留着。
    numbered = not reply.is_qq(event)
    try:
        with (
            rogue_actions.numbered_options(numbered),
            rogue_actions.line_links(),
        ):
            if raw == "":
                usage = _never_empty(text("rogue.usage", ""), _rogue_usage())
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
                msg = _never_empty(text("rogue.usage", ""), _rogue_usage())
    except ValueError as exc:
        await reply.finish(pet_rogue_matcher, f"❌ {exc}")
    except Exception as exc:  # 未知错误也要给用户回执，不静默吞掉对话
        log.warning(f"宠物.肉鸽 处理失败：{exc!r}")
        await reply.finish(pet_rogue_matcher, "❌ 肉鸽指令执行出错，请查看机器人日志。")

    await pet_core.save_pet(pet)
    # 局内每一步都给「当前可选动作」的按钮：让玩家按编号手打太容易出错，
    # 而肉鸽是纯编号推进，按钮几乎是唯一顺手的交互方式。
    choices = rogue_actions.choices_from_rogue(rogue)
    if not choices:
        # 没有局内选项时退回固定菜单（开局 / 进度 / 撤退）
        choices = menus.rogue_menu(running=bool(getattr(rogue, "floor", 0)))
    await reply.finish(
        pet_rogue_matcher,
        _never_empty(msg, _rogue_usage()),
        choices,
    )


def _shortage_actions(exc) -> str:
    """资源不足提示后面的可点命令（仅 QQ 端有内容）。"""
    hints = getattr(exc, "hints", None) or []
    if not hints:
        return ""
    parts = [
        menus.inline(str(h.get("label") or "去做"), str(h["path"]))
        for h in hints
        # 没有 path 的条目生成不出有效指令，直接丢掉——空命令会让用户点到 "/"
        if str(h.get("path") or "").strip()
    ]
    parts = [p for p in parts if p]
    if not parts:
        # OneBot 端：命令已经写在正文里了（core 层生成的文字），不必重复
        return ""
    return "\n> " + " ".join(parts)


@pet_train_matcher.handle()
async def handle_train(event: Event):
    await _guard(pet_train_matcher)
    player_id = _player_id(event)
    pet = await _load_pet(pet_train_matcher, player_id)
    await _settle(pet_train_matcher, pet, player_id)

    try:
        result = await interaction.train(pet, player_id)
    except interaction.ResourceShortageError as exc:
        # 资源不足时提示里带着「先喂食 / 先玩耍」这类指令。把命令内嵌到那一行，
        # 用户点一下就能去做，不必照着文字手打。
        await reply.finish(
            pet_train_matcher,
            f"❌ {exc}" + _shortage_actions(exc),
        )
    except ValueError as exc:
        await reply.finish(pet_train_matcher, f"❌ {exc}")

    await pet_core.save_pet(pet)
    await reply.finish(
        pet_train_matcher,
        format_action_result("train", result),
        menus.train_menu(),
    )
