from apscheduler.triggers.interval import IntervalTrigger
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from ATRI.utils.apscheduler import scheduler

time_type = {'0': 0, '1': 5 * 60, '2': 10 * 60, '3': 15 * 60, '4': 20 * 60, '5': 30 * 60, '6': 45 * 60, '7': 60 * 60}


class Team:
    def __init__(self, team_name, leader_id):
        self.team_name = team_name
        self.leader = leader_id
        self.members = []


class TeamManager:
    def __init__(self, group_id):
        self.teams = {}
        '''{leader_id: Team}'''
        self.team_ids = {}
        '''{team_name: leader_id}'''
        self.user_info = {}
        '''{用户QQ号:{
        "name": 用户昵称,
        "id": leader_id
        }}'''
        self.group_id = group_id

    def add_user(self, user_id, user_name, leader_id):
        self.user_info[user_id] = {'name': user_name, 'id': leader_id}

    def new_team(self, team_name, user_id, user_name, t_type, bot, group_id):
        self.add_user(user_id, user_name, user_id)
        self.team_ids[team_name] = user_id
        self.teams[user_id] = Team(team_name, user_id)
        scheduler.add_job(self.show_info, IntervalTrigger(seconds=time_type[t_type]), id=f'{self.group_id}-{user_id}',
                          args=[user_id, bot, group_id])

    def join_team(self, user_id, leader_id, user_name):
        self.add_user(user_id, user_name, leader_id)
        team: Team = self.teams[leader_id]
        team.members.append(user_id)

    def exit_team(self, user_id):
        message = Message()
        if user_id in self.teams:
            message = self.del_team(user_id)
        else:
            team_id = self.user_info[user_id]['id']
            message.append(
                f'成员 {self.user_info[user_id]["name"]} 退出队伍 {self.teams[team_id].team_name} 成功！\n队长')
            message.append(MessageSegment.at(team_id))
            message.append('请注意')
            self.teams[team_id].members.remove(user_id)
            self.user_info.pop(user_id)
        return message

    def del_team(self, team_id):
        scheduler.remove_job(f'{self.group_id}-{team_id}')
        message = Message().append(f'队伍 {self.teams[team_id].team_name} 结束成功！')
        members = self.teams[team_id].members
        if members:
            message.append('\n成员')
            for member in members:
                self.user_info.pop(member)
                message.append(MessageSegment.at(member))
            message.append('请注意')
        self.team_ids.pop(self.teams[team_id].team_name)
        self.teams.pop(team_id)
        self.user_info.pop(team_id)
        return message

    def force_del_team(self, team_id):
        scheduler.remove_job(f'{self.group_id}-{team_id}')
        message = Message().append(MessageSegment.at(team_id))
        message.append(f'你的队伍 {self.teams[team_id].team_name} 被强制结束了！')
        members = self.teams[team_id].members
        if members:
            message.append('\n成员')
            for member in members:
                self.user_info.pop(member)
                message.append(MessageSegment.at(member))
            message.append('请注意')
        self.team_ids.pop(self.teams[team_id].team_name)
        self.teams.pop(team_id)
        self.user_info.pop(team_id)
        return message

    async def show_info(self, team_id, bot, group_id):
        info = f'{self.teams[team_id].team_name} 正在招人中...\n发起人:{self.user_info[team_id]["name"]}\n成员:'
        for member in self.teams[team_id].members:
            info += f'\n{self.user_info[member][["name"]]}'
        await bot.send_group_msg(group_id=group_id, message=info)

    def notice(self, team_id) -> Message:
        info = Message()
        info.append(
            f'{self.teams[team_id].team_name} 的成员请做好准备！\n收到请回复发起人 {self.user_info[team_id][["name"]]}\n')
        for member in self.teams[team_id].members:
            info.append(MessageSegment.at(member))
        info.append('\n同时也欢迎新队员加入！')
        return info


def check_manager(group_id):
    if group_id in team_manager:
        return
    team_manager[group_id] = TeamManager(group_id)


team_manager = {}
