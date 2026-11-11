class FishingException(Exception):
    def __init__(self, message):
        self.message = message


class NoFishingRod(FishingException):
    def __init__(self):
        super().__init__("未装备鱼竿")


class NoBait(FishingException):
    def __init__(self):
        super().__init__("未装备鱼饵")
