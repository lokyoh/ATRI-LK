class FishingTackle:
    def __init__(self, data: dict):
        self.name = data['name']
        self.description = data['description']
        self.durable = data['durable']
        self.waiting_time = data.get('waiting_time', 0)
        self.fishi_quality = data.get('fishi_quality', 0)
        self.reaction_time = data.get('reaction_time', 0)
        self.treasure_chance = data.get('treasure_chance', 0)
        self.fishi_chance = data.get('fishi_chance', 0)


fishing_tackle_dict = {}
