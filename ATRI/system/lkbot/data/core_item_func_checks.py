from .user import users
from .item_func import ConditionNotMet


def exp_mul_check(user_id, args):
    data = users.get_user_data(user_id)
    if data.exp_mul == 100 or data.exp_mul == args.get('multiplier', 200):
        return
    if data.exp_mul_count == 0:
        return
    raise ConditionNotMet("已经存在不同值的经验加成，请等待次数用尽")


def love_mul_check(user_id, args):
    data = users.get_user_data(user_id)
    if data.love_mul == 100 or data.love_mul == args.get('multiplier', 200):
        return
    if data.love_mul_count == 0:
        return
    raise ConditionNotMet("已经存在不同值的好感加成，请等待次数用尽")
