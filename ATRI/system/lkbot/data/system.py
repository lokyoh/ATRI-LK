from ATRI.exceptions import BotRuntimeError


class LkSystemData:
    """系统运行时参数"""
    exp_mul = 100
    love_mul = 100

    def set_exp_mul(self, number: int) -> None:
        """设置全局经验倍率"""
        if 0 < number <= 10000:
            self.exp_mul = number
        else:
            raise BotRuntimeError(f"在设置经验倍率时出现问题,请检查数据`{number}`")

    def set_love_mul(self, number: int) -> None:
        """设置全局好感倍率"""
        if 0 < number <= 10000:
            self.love_mul = number
        else:
            raise BotRuntimeError(f"在设置好感倍率时出现问题,请检查数据`{number}`")


lk_data = LkSystemData()
"""系统运行时参数"""
