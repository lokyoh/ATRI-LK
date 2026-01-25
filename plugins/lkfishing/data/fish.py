import datetime
import random


class FishingItem:
    def __init__(self, item_data):
        self.name = item_data['name']
        self.description = item_data['description']
        self.price = item_data['price']
        self.position = item_data['position']
        self.xp = item_data['xp']
        self.weight = item_data['weight']

    def can_catch(self, weather) -> bool:
        return True


class Fish(FishingItem):
    def __init__(self, fish_data):
        super().__init__(fish_data)
        self.time = fish_data['time']
        self.season = fish_data['season']
        self.weather = fish_data['weather']
        self.size = fish_data['size']
        self.difficulty = fish_data['difficulty']

    def can_catch(self, weather) -> bool:
        dt = datetime.datetime.now()
        if self.season:
            month = dt.month
            season = "winter"
            if 3 <= month <= 5:
                season = "spring"
            elif 6 <= month <= 8:
                season = "summer"
            elif 9 <= month <= 11:
                season = "fall"
            if season not in self.season:
                return False
        if self.time:
            hour = dt.hour
            start = self.time["start"]
            end = self.time["end"]
            if start < end:
                if not start <= hour < end:
                    return False
            else:
                if end <= hour < start:
                    return False
        if self.weather:
            if weather == 0:
                if 'sun' not in self.weather:
                    return False
            else:
                if 'rain' not in self.weather:
                    return False
        return True


class FishData:
    def __init__(self, fish: FishingItem | Fish, user_data):
        self.fish = fish
        self.quality = None
        if type(fish) == Fish:
            rand_q = random.randint(0, int(fish.difficulty * user_data.level / 50))
            rand_q += random.randint(0, user_data.get_fishing_rod().quality)
            rand_q += random.randint(0, user_data.get_bait().quality)
            f_t = user_data.get_fishing_tackle()
            if f_t:
                rand_q += random.randint(0, f_t.fishi_quality)
            rand_q += random.randint(1, int(fish.difficulty * 1.1))
            per = 0.25
            if rand_q > fish.difficulty * 3:
                self.quality = '铱'
                per = 1
            elif rand_q > fish.difficulty * 2:
                self.quality = '金'
                per = 0.75
            elif rand_q > fish.difficulty:
                self.quality = '银'
                per = 0.5
            self.length = random.randint(self.fish.size['min'],
                                         self.fish.size['min'] + int((fish.size['max'] - self.fish.size['min']) * per))
        else:
            self.length = None
