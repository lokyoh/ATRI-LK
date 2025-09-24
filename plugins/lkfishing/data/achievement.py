from ..database import AchievementData


class Achievement:
    def __init__(self, name, description, func):
        self.name = name
        self.description = description
        self.func = func


achievements = []


def add_achievement(name, description, func):
    achievements.append(Achievement(name, description, func))


def check_achievement(achievement: AchievementData, fish_data, msg):
    for ach in achievements:
        if ach.name in achievement.achieve:
            continue
        achieve = ach.func(achievement=achievement, fish_data=fish_data)
        if achieve:
            achievement.achieve.append(ach.name)
            msg.append(f'[{ach.name}]-{ach.description}')


def fishing_times_achi(times):
    def func(**kwargs):
        achievement: AchievementData = kwargs['achievement']
        if achievement.achievement_data.get('times', 0) >= times:
            return True
        return False

    return func


def fishing_types_achi(times):
    def func(**kwargs):
        achievement: AchievementData = kwargs['achievement']
        _times = 0
        for f in achievement.fish_data:
            if 'length' in achievement.fish_data[f]:
                _times += 1
        if _times >= times:
            return True
        return False

    return func


def load_achievements():
    achievements.append(Achievement('第一次尝试!', '第一次尝试钓鱼,从现在开始你的钓鱼人生吧!', fishing_times_achi(1)))
    achievements.append(Achievement('略有心得', '钓鱼10次', fishing_times_achi(10)))
    achievements.append(Achievement('乐此不疲', '钓鱼50次', fishing_times_achi(50)))
    achievements.append(Achievement('风雨无阻', '钓鱼100次', fishing_times_achi(100)))
    achievements.append(Achievement('塘边常客', '钓鱼250次', fishing_times_achi(250)))
    achievements.append(Achievement('痴迷钓手', '钓鱼500次', fishing_times_achi(500)))
    achievements.append(Achievement('时间管理大师', '钓鱼1000次', fishing_times_achi(1000)))
    achievements.append(Achievement('开图鉴啦', '钓到3种不同鱼类', fishing_types_achi(3)))
    achievements.append(Achievement('口味广泛', '钓到5种不同鱼类', fishing_types_achi(5)))
    achievements.append(Achievement('收藏入门', '钓到10种不同鱼类', fishing_types_achi(10)))
    achievements.append(Achievement('见多识广', '钓到20种不同鱼类', fishing_types_achi(20)))
    achievements.append(Achievement('鱼类百科', '钓到35种不同鱼类', fishing_types_achi(35)))

    def get_max_length(**kwargs):
        fish_data = kwargs['fish_data']
        if fish_data.length and fish_data.length == fish_data.fish.size['max']:
            return True
        return False

    achievements.append(Achievement('好长!!!', '钓到一次最长长度的鱼', get_max_length))

    def get_max_quality(**kwargs):
        fish_data = kwargs['fish_data']
        if fish_data.quality and fish_data.quality == '铱':
            return True
        return False

    achievements.append(Achievement('极品!!!', '钓到一次铱品质的鱼', get_max_quality))
