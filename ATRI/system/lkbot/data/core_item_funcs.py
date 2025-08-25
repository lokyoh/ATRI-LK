from .user import UserData


def exp_mul(user_data: UserData, args):
    name = user_data.name
    multiplier = args.get('multiplier', 200)
    times = args.get('times', 1)
    user_data.exp_mul_change(multiplier, times)
    return f'{name}获得经验*{multiplier}%{times}次，共{user_data.exp_mul_count}次'


def love_mul(user_data: UserData, args):
    name = user_data.name
    multiplier = args.get('multiplier', 200)
    times = args.get('times', 1)
    user_data.love_mul_change(multiplier, times)
    return f'{name}获得好感*{multiplier}%{times}次，共{user_data.love_mul_count}次'
