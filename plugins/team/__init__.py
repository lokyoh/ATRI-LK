from random import choice

from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11.event import GroupMessageEvent
from nonebot.adapters.onebot.v11.helpers import Cooldown
from nonebot.adapters.onebot.v11.message import Message, MessageSegment
from nonebot.matcher import Matcher
from nonebot.params import CommandArg, ArgPlainText

from ATRI.service import Service
from ATRI.permission import ADMIN
from ATRI.system.lkbot.util import lk_util

from .team_data import team_manager, check_manager, time_type

plugin = Service("组队插件").document("l_o_o_k的组队插件v1.0.0").type(Service.ServiceType.FUNCTION).main_cmd(
    "/team")

_lmt_notice = ["慢...慢一..点❤", "冷静1下", "歇会歇会~~", "呜呜...别急", "太快了...受不了", "不要这么快呀"]

new_team = plugin.cmd_as_group(cmd='组队', docs='创建一个新队伍')


@new_team.handle()
async def _(event: GroupMessageEvent, matcher: Matcher, args: Message = CommandArg()):
    user_id = event.get_user_id()
    group_id = event.group_id
    check_manager(group_id)
    if user_id in team_manager[group_id].teams or user_id in team_manager[group_id].user_info:
        await new_team.finish("你已经在队伍中了")
    name = args.extract_plain_text()
    if name:
        matcher.set_arg("new_team_name", args)


@new_team.got("new_team_name", "请输入你要创建的队伍名称")
@new_team.got("team_time_type", '''请选择提示间隔:
0:不提示 1:5分钟
2:10分钟 3:15分钟
4:20分钟 5:30分钟
6:45分钟 7:60分钟''')
async def _(event: GroupMessageEvent, bot: Bot, name: str = ArgPlainText("new_team_name"),
            time_type_: str = ArgPlainText("team_time_type")):
    if time_type_ not in time_type:
        await new_team.finish("请输入时间间隔前的数字")
    name = lk_util.clean_str(name)
    user_id = event.get_user_id()
    group_id = event.group_id
    user_info = await bot.get_group_member_info(group_id=group_id, user_id=int(user_id))
    team_manager[group_id].new_team(name, user_id, user_info["nickname"], time_type_, bot, group_id)
    await team_manager[group_id].show_info(user_id, bot, group_id)


join_team = plugin.cmd_as_group(cmd='加入', docs='加入指定的队伍')


@join_team.handle()
async def _(event: GroupMessageEvent, matcher: Matcher, args: Message = CommandArg()):
    user_id = event.get_user_id()
    group_id = event.group_id
    check_manager(group_id)
    if user_id in team_manager[group_id].teams or user_id in team_manager[group_id].user_info:
        await join_team.finish('你已经在队伍中了')
    name = args.extract_plain_text()
    if name:
        matcher.set_arg("join_team_name", args)


@join_team.got('join_team_name', '请输入要加入的队伍名称或队长的QQ号')
async def _(event: GroupMessageEvent, bot: Bot, team_pass: str = ArgPlainText('join_team_name')):
    team_pass = lk_util.clean_str(team_pass)
    group_id = event.group_id
    if team_pass in team_manager[group_id].team_ids:
        team_pass = team_manager[group_id].team_ids[team_pass]
    if team_pass in team_manager[group_id].teams:
        user_id = event.get_user_id()
        group_id = int(event.group_id)
        user_info = await bot.get_group_member_info(group_id=group_id, user_id=int(user_id))
        team_manager[group_id].join_team(user_id, team_pass, user_info["nickname"])
        await join_team.finish(Message()
                               .append(f'加入队伍 {team_manager[group_id].teams[team_pass].team_name}成功了\n')
                               .append(MessageSegment.at(team_pass))
                               .append("请注意哦")
                               )
    else:
        await join_team.finish('没有找到指定队伍哦')


exit_team = plugin.cmd_as_group(cmd='退出', docs='退出自己所在的队伍')


@exit_team.handle()
async def _(event: GroupMessageEvent):
    user_id = event.get_user_id()
    group_id = event.group_id
    check_manager(group_id)
    if user_id in team_manager[group_id].user_info:
        message: Message = team_manager[group_id].exit_team(user_id)
        await exit_team.finish(message)
    await exit_team.finish('你没有加入队伍哦')


del_team = plugin.cmd_as_group(cmd='结束', docs='结束自己创建的队伍')


@del_team.handle()
async def _(event: GroupMessageEvent):
    user_id = event.get_user_id()
    group_id = event.group_id
    check_manager(group_id)
    if user_id in team_manager[group_id].teams:
        message: Message = team_manager[group_id].del_team(user_id)
        await del_team.finish(message)
    if user_id in team_manager[group_id].user_info:
        await del_team.finish('你不是队长哟')
    await del_team.finish('你没有加入队伍哦')


notice_team = plugin.cmd_as_group(cmd='提醒', docs='提醒自己队伍的成员')


@notice_team.handle([Cooldown(30, prompt=choice(_lmt_notice))])
async def _(event: GroupMessageEvent):
    user_id = event.get_user_id()
    group_id = event.group_id
    if user_id not in team_manager[group_id].teams:
        await new_team.finish("你还没有创建队伍哦")
    await notice_team.finish(team_manager[group_id].notice(user_id))


force_del_team = plugin.cmd_as_group(cmd='强制结束', docs='强制结束一个队伍', permission=ADMIN)


@force_del_team.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    group_id = event.group_id
    check_manager(group_id)
    team_pass = lk_util.clean_str(args.extract_plain_text())
    if team_pass in team_manager[group_id].team_ids:
        team_pass = team_manager[group_id].team_ids[team_pass]
    if team_pass in team_manager[group_id].teams:
        message: Message = team_manager[group_id].force_del_team(team_pass)
        await force_del_team.finish(message)
    await force_del_team.finish('没找到指定队伍')
