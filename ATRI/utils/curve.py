import random


class IntToBoolRandom:
    """数字转布尔随机器-cpu型，返回True的概率随输入增大而增大且增大数值越来越大"""

    def __init__(self, random_num, max_num):
        """
        :param random_num: 概率数字，>=0，越大后期增幅越小，前期概率越高
        :param max_num: 最多尝试次数，>=0，越大取值范围越大，前期概率越低
        """
        self.random_num = random_num
        self.max_num = max_num

    def get_result(self, num_input: int) -> bool:
        num = self.max_num - num_input
        i = 0
        while i < num:
            if random.randint(1, self.random_num) == int(self.random_num / 2):
                return False
            i += 1
        return True


class LvlManager:
    def __init__(self, base_num, multiple):
        self.base_num = base_num
        self.multiple = multiple

    # exp max = 2,147,483,647
    def to_lvl(self, exp: int):
        result = 0
        for i in range(100):
            lvl_exp = self.base_num * int((i + 1) ** self.multiple)
            result += lvl_exp
            if exp < result:
                return i

    def get_left_exp(self, exp, lvl):
        result = 0
        for i in range(lvl):
            lvl_exp = self.base_num * int((i + 1) ** self.multiple)
            result += lvl_exp
        return exp - result

    def get_lvl_exp(self, lvl: int):
        return self.base_num * int((lvl + 1) ** self.multiple)
