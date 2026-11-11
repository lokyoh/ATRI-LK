import random


class TreasureManager:
    def __init__(self):
        self.loot_list = {}

    def add_loot(self, loot_name, chance, item, num):
        if type(item) is str:
            item = [item]
        if type(num) is int:
            num = [num]
        self.loot_list[loot_name] = {
            'chance': chance,
            'items': item,
            'nums': num,
        }

    def get_value_list(self):
        return list(value['chance'] for value in self.loot_list.values())

    @staticmethod
    def add_item_to_treasure(t_l, item, num):
        if item in t_l:
            t_l[item] += num
        else:
            t_l[item] = num

    def gene_treasure(self, offset: int = 0):
        rand = random.randint(1, 1000) + offset
        if rand <= 750:
            i = 1
        elif rand <= 950:
            i = 2
        else:
            i = 3
        ll_names = random.choices(list(self.loot_list.keys()), self.get_value_list(), k=i)
        t_l = {}
        for ll_name in ll_names:
            l_l = self.loot_list[ll_name]
            for i in range(len(l_l['items'])):
                self.add_item_to_treasure(t_l, l_l['items'][i], l_l['nums'][i])
        return t_l

    def clear(self):
        self.loot_list.clear()

    def add_dict_loot(self, loot_name, data):
        self.add_loot(loot_name, data['chance'], data['items'], data.get('nums', 1))


treasure_manager = TreasureManager()
