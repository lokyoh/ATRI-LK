from enum import Enum

class Season(Enum):
    """春3-5，夏6-8，秋9-11，冬12-2"""
    SPRING = "春季"
    SUMNER = "夏季"
    AUTUMN = "秋季"
    WINTER = "冬季"
    SPRSUM = "春夏两季"
    SUMAUT = "夏秋两季"
    SPSUAU = "春夏秋三季"
    ALL = "全季"

    def get_seasons(self):
        if self == Season.SPRSUM:
            return [Season.SPRING, Season.SUMNER]
        if self == Season.SUMAUT:
            return [Season.SUMNER, Season.AUTUMN]
        if self == Season.SPSUAU:
            return [Season.SPRING, Season.SUMNER, Season.AUTUMN]
        if self == Season.ALL:
            return [Season.SPRING, Season.SUMNER, Season.AUTUMN, Season.WINTER]
        return [self]


class Month(Enum):
    JANUARY = 1
    FEBRUARY = 2
    MARCH = 3
    APRIL = 4
    MAY = 5
    JUNE = 6
    JULY = 7
    AUGUST = 8
    SEPTEMBER = 9
    OCTOBER = 10
    NOVEMBER = 11
    DECEMBER = 12

    def to_season(self) -> Season:
        if 3 <= self.value <= 5:
            return Season.SPRING
        elif 6 <= self.value <= 8:
            return Season.SUMNER
        elif 9 <= self.value <= 11:
            return Season.AUTUMN
        else:
            return Season.WINTER