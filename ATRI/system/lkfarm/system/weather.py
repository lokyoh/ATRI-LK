from random import randint


def get_w_r(num: int):
    return randint(1, 1000) < num


def get_weather(month: int, day: int, weather: int):
    """0:晴 1:雨 2:雷雨 3:雪"""
    if day == 1:
        return 0
    base_rate = 138
    if weather > 0:
        base_rate -= 5
    if 3 <= month <= 5:
        if get_w_r(base_rate):
            if get_w_r(250):
                return 2
            else:
                return 1
        else:
            return 0
    elif 6 <= month <= 8:
        base_rate = 126
        base_rate += month * 8 + day * 2 - 50
        if get_w_r(base_rate):
            if get_w_r(170):
                return 2
            else:
                return 1
        else:
            return 0
    elif 9 <= month <= 11:
        if get_w_r(base_rate):
            if get_w_r(140):
                return 2
            else:
                return 1
        return 0
    else:
        if get_w_r(630):
            return 3
        else:
            return 0
