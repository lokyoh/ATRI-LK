from .user import users


def exp_mul(user_id, args):
    name = users.get_user_name(user_id)
    multiplier = args.get('multiplier', 200)
    times = args.get('times', 1)
    users.exp_mul_change(user_id, multiplier, times)
    return f'{name}获得经验*{multiplier}%{times}次，共{users.get_user_data(user_id).exp_mul_count}次'


def love_mul(user_id, args):
    name = users.get_user_name(user_id)
    multiplier = args.get('multiplier', 200)
    times = args.get('times', 1)
    users.love_mul_change(user_id, multiplier, times)
    return f'{name}获得好感*{multiplier}%{times}次，共{users.get_user_data(user_id).love_mul_count}次'
