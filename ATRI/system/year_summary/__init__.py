from pathlib import Path
from typing import Any

from nonebot.adapters import Bot, Event, Message
from nonebot.params import CommandArg

from ATRI.bot.statistics.adapter import get_group_id
from ATRI.bot.utils import BotUtils
from ATRI.dir import RES_DIR
from ATRI.log import log
from ATRI.message import ATRIMessage, img_msg
from ATRI.permission import is_master
from ATRI.service import Service
from ATRI.system.htmlrender import template_to_pic
from ATRI.utils.datetime import now

from .data_source import (
    collect_personal_summary,
    collect_scope_summary,
    format_personal_report,
    format_scope_report,
    parse_target_date,
    personal_report_context,
    resolve_window,
    scope_report_context,
)

plugin = Service(
    "年终总结",
    "回顾从年初到目标日期的统计数据",
    "0.1.0",
    Service.ServiceType.SYSTEM,
)

year_summary = plugin.on_command(
    "年终总结",
    "回顾从年初到目标日期的数据；群聊先发群总结，再发你的个人报告",
    aliases={"年度总结", "年度报告", "年终报告"},
)

_REPORT_TEMPLATE_DIR = (RES_DIR / "html" / "year_summary").resolve()
_REPORT_VIEWPORT = {"width": 720, "height": 10}


async def _report_message(event: Event, text: str, context: dict[str, Any]):
    """QQ 官方发 Markdown，OneBot 用与帮助页同款主题的模板渲染图片。"""
    am = ATRIMessage(event)
    if am.mtype == 1:
        return await am.md(text)
    pic = await template_to_pic(
        template_path=str(_REPORT_TEMPLATE_DIR),
        template_name="report.html",
        templates=context,
        pages={
            "viewport": _REPORT_VIEWPORT,
            "base_url": f"file://{Path.cwd()}",
        },
    )
    return img_msg(pic)


async def _group_label(bot: Bot, group_id: str) -> str:
    """尽量取群名，取不到就退回群号。"""
    try:
        for group in await BotUtils.get_group_list(bot):
            if str(group.group_id) == str(group_id):
                return f"群「{group.group_name}」"
    except Exception as e:
        log.debug(f"获取群名称失败：{e}")
    return f"群 {group_id}"


@year_summary.handle()
async def _(bot: Bot, event: Event, args: Message = CommandArg()):
    tokens = args.extract_plain_text().strip().split()
    global_scope = False
    if tokens and tokens[0].lower() in {"全局", "all"}:
        global_scope = True
        tokens.pop(0)

    if global_scope and not is_master(bot, event):
        await year_summary.finish("只有主人才能查看全局年终总结哦")

    today = now().date()
    try:
        target = parse_target_date(tokens[0] if tokens else "", today)
    except ValueError as exc:
        await year_summary.finish(str(exc))

    if target > today:
        await year_summary.finish("目标日期不能晚于今天哦")

    window = resolve_window(target)
    group_id = get_group_id(event)
    user_id = event.get_user_id()
    bot_id = bot.self_id

    if global_scope:
        scope = await collect_scope_summary(
            bot_id=bot_id,
            window=window,
            global_scope=True,
            scope_label="全局",
        )
        await year_summary.send(
            await _report_message(
                event, format_scope_report(scope), scope_report_context(scope)
            )
        )
        personal = await collect_personal_summary(
            bot_id=bot_id,
            window=window,
            user_id=user_id,
            scope_label="全部聊天",
            scope_total=scope.message_total,
        )
        await year_summary.finish(
            await _report_message(
                event,
                format_personal_report(personal),
                personal_report_context(personal),
            )
        )
    elif group_id:
        label = await _group_label(bot, group_id)
        scope = await collect_scope_summary(
            bot_id=bot_id,
            window=window,
            group_id=group_id,
            scope_label=label,
        )
        await year_summary.send(
            await _report_message(
                event, format_scope_report(scope), scope_report_context(scope)
            )
        )
        personal = await collect_personal_summary(
            bot_id=bot_id,
            window=window,
            user_id=user_id,
            group_id=group_id,
            scope_label=label,
            scope_total=scope.message_total,
        )
        await year_summary.finish(
            await _report_message(
                event,
                format_personal_report(personal),
                personal_report_context(personal),
            )
        )
    else:
        personal = await collect_personal_summary(
            bot_id=bot_id,
            window=window,
            user_id=user_id,
            scope_label="全部聊天",
        )
        await year_summary.finish(
            await _report_message(
                event,
                format_personal_report(personal),
                personal_report_context(personal),
            )
        )
