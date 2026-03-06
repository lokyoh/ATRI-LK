import os
import random
import time
import yaml

from ATRI.dir import RES_DATA_DIR
from ATRI.log import log
from ATRI.exceptions import str_traceback
from ATRI.system.lkapi.bot.events import item_loading_events
from ATRI.system.lkapi.entity.item import items, Item, ItemType
from ATRI.system.lkapi.entity.shop import shops, Shop
from ATRI.system.lkapi.entity.user import get_user_data

from plugins.lkfarm.system.farm_user import user_farm_datas, get_user_farm_data

from .data.achievement import load_achievements, achievements
from .data.bait import bait_dict, Bait
from .data.exception import FishingException
from .data.fish import FishData, Fish, FishingItem
from .data.fishing_rod import fishing_rod_dict, FishingRod
from .data.fishing_tackle import fishing_tackle_dict, FishingTackle
from .data.treasure import treasure_manager
from .data.user import get_fish_user_data, FishingUser

DATA_PATH = RES_DATA_DIR / "lkfishing"


class FishingController:
    player_fishing_data = {}
    fish_area_data = {}
    item_area_data = {}
    fishing_shop = Shop('渔具商店', '亚托莉售卖各种渔具的地方')

    @classmethod
    def add_new_fishing_man(cls, user_id) -> int:
        info: FishingUser = get_fish_user_data(user_id)
        position = info.position
        if position not in cls.fish_area_data:
            raise FishingException('你所在的位置不能钓鱼!')
        if user_id in cls.player_fishing_data:
            raise FishingException('你已经在钓鱼了!')
        bait = info.get_bait()
        fishing_rod = info.get_fishing_rod()
        wait_time = random.randint(10, 30) - bait.time - fishing_rod.time
        fishing_tackle = info.get_fishing_tackle()
        if fishing_tackle:
            wait_time -= fishing_tackle.waiting_time
        if wait_time < 1:
            wait_time = 1
        with get_user_farm_data(user_id) as user_farm_data:
            if not user_farm_data.endurance_change(-40):
                raise FishingException('体力不足!')
        cls.player_fishing_data[user_id] = None
        return int(wait_time)

    @classmethod
    def fish_bite(cls, user_id):
        if user_id not in cls.player_fishing_data:
            raise FishingException(None)
        cls.player_fishing_data[user_id] = time.time()

    @classmethod
    def take_up(cls, user_id) -> tuple[dict, list]:
        # 检测是否在钓鱼
        if user_id not in cls.player_fishing_data:
            raise FishingException('你没有在钓鱼哦!')
        # 删除钓鱼状态
        del cls.player_fishing_data[user_id]
        with get_fish_user_data(user_id) as info:
            # 检测鱼是否上钩
            if cls.player_fishing_data[user_id] is None:
                # 进行数据处理
                info.increase_damage()
                raise FishingException('🐟还没有上钩呢...请重新钓鱼吧')
            # 检测钓鱼是否超时
            now = time.time()
            max_wait_time = 15.
            if trackle := info.get_fishing_tackle():
                max_wait_time += trackle.reaction_time
            if now - cls.player_fishing_data[user_id] > max_wait_time:
                # 进行数据处理
                info.increase_damage()
                info.use_bait()
                if info.get_fishing_tackle():
                    info.increase_tackle_damage()
                raise FishingException('🐟已经跑掉了...')
            # 进行数据处理
            # 生成鱼
            fish_data = cls.gene_fish(info)
            # 生成宝藏
            t_l = cls.gene_treasure(info)
            # 处理钓鱼用户数据
            info.increase_damage()
            info.use_bait()
            if info.get_fishing_tackle():
                info.increase_tackle_damage()
            # 为玩家添加钓鱼收获
            with get_user_data(user_id) as user_data:
                for _fish, _num in fish_data.items():
                    user_data.item_num_change(f'{_fish.fish.name}{f'-{_fish.quality}' if _fish.quality else ''}', _num)
                    info.add_xp(_fish.fish.xp)
                    achis = info.add_fish(_fish)
                if t_l:
                    for _item, _num in t_l.items():
                        user_data.item_num_change(_item, _num)
                        fish_data[_item] = _num
            return fish_data, achis

    @classmethod
    def get_fish_weight(cls, position, weather):
        if position not in cls.fish_area_data:
            raise ValueError('地域错误')
        fish_list = []
        weight_list = []
        all_fish_list = cls.fish_area_data[position]
        for fish in all_fish_list:
            if fish.can_catch(weather):
                fish_list.append(fish)
                weight_list.append(fish.weight)
        return fish_list, weight_list

    @classmethod
    def get_fish_item_weight(cls, position, weather):
        item_list = []
        item_weight_list = []
        if position in cls.item_area_data:
            all_item_list = cls.item_area_data[position]
            for item in all_item_list:
                if item.can_catch(weather):
                    item_list.append(item)
                    item_weight_list.append(item.weight)
        return item_list, item_weight_list

    @classmethod
    def gene_fish(cls, user_data: FishingUser) -> dict:
        fish_list, weight_list = cls.get_fish_weight(user_data.position, user_farm_datas.weather)
        item_list, item_weight_list = cls.get_fish_item_weight(user_data.position, user_farm_datas.weather)
        if len(fish_list) == 0 and len(item_list) == 0:
            raise FishingException('该地域此时没有任何鱼类!!!\n请反馈...')
        fishi_chance = .1
        if tackle := user_data.get_fishing_tackle():
            fishi_chance += tackle.fishi_chance
        if (len(item_list) != 0 and random.random() < fishi_chance) or len(fish_list) == 0:
            fish = random.choices(item_list, weights=item_weight_list)[0]
        else:
            fish = random.choices(fish_list, weights=weight_list)[0]
        return {FishData(fish, user_data): 1}

    @classmethod
    def gene_treasure(cls, user_data: FishingUser) -> dict | None:
        treasure_chance = 100
        tackle = user_data.get_fishing_tackle()
        if tackle:
            treasure_chance += tackle.treasure_chance
        if random.randint(1, 10000) <= treasure_chance:
            return treasure_manager.gene_treasure()
        return None

    @classmethod
    def load_fish_data(cls):
        fish_path = DATA_PATH / "fish"
        fish_files = os.listdir(fish_path)
        for fish_file in fish_files:
            fish_data = yaml.safe_load((fish_path / fish_file).read_bytes())
            if fish_data is None:
                continue
            for key in fish_data:
                try:
                    fish = Fish(fish_data[key])
                    for p in fish.position:
                        if p not in cls.fish_area_data:
                            cls.fish_area_data[p] = []
                        cls.fish_area_data[p].append(fish)
                    items.register(Item(fish.name, ItemType.FISH, fish.description, fish.price))
                    items.register(Item(f'{fish.name}-银', ItemType.FISH, fish.description, int(fish.price * 1.25)))
                    items.register(Item(f'{fish.name}-金', ItemType.FISH, fish.description, int(fish.price * 1.5)))
                    items.register(Item(f'{fish.name}-铱', ItemType.FISH, fish.description, int(fish.price * 2)))
                except Exception as e:
                    log.error(f'fish-{fish_file}-{key}无效鱼配置:\n{str_traceback(e)}')
        fishing_item_path = DATA_PATH / "fishing_item"
        fishing_item_files = os.listdir(fishing_item_path)
        for fishing_item_file in fishing_item_files:
            item_data = yaml.safe_load((fishing_item_path / fishing_item_file).read_bytes())
            if item_data is None:
                continue
            for key in item_data:
                try:
                    item = FishingItem(item_data[key])
                    for p in item.position:
                        if p not in cls.item_area_data:
                            cls.item_area_data[p] = []
                        cls.item_area_data[p].append(item)
                    items.register(
                        Item(item.name, ItemType(item_data[key].get('type', '其他')), item.description, item.price))
                except Exception as e:
                    log.error(f'fishing_item-{fishing_item_file}-{key}无效物品配置:\n{str_traceback(e)}')

    @classmethod
    def load_fishing_rod_data(cls):
        rod_path = DATA_PATH / "rod"
        rod_files = os.listdir(rod_path)
        for rod_file in rod_files:
            rod_data = yaml.safe_load((rod_path / rod_file).read_bytes())
            if rod_data is None:
                continue
            for key in rod_data:
                try:
                    rod = FishingRod(rod_data[key])
                    fishing_rod_dict[rod.name] = rod
                    item = Item(rod.name, ItemType.TOOL, rod.description, 0)
                    items.register(item)
                    cls.fishing_shop.add_goods(item, rod_data[key]['price'])
                except Exception as e:
                    log.error(f'rod-{rod_file}-{key}无效鱼竿配置:\n{str_traceback(e)}')

    @classmethod
    def load_bait_data(cls):
        bait_path = DATA_PATH / "bait"
        bait_files = os.listdir(bait_path)
        for bait_file in bait_files:
            bait_data = yaml.safe_load((bait_path / bait_file).read_bytes())
            if bait_data is None:
                continue
            for key in bait_data:
                try:
                    bait = Bait(bait_data[key])
                    bait_dict[bait.name] = bait
                    item = Item(bait.name, ItemType.PROP, bait.description, 0)
                    items.register(item)
                    cls.fishing_shop.add_goods(item, bait_data[key]['price'])
                except Exception as e:
                    log.error(f'bait-{bait_file}-{key}无效鱼饵配置:\n{str_traceback(e)}')

    @classmethod
    def load_fishing_tackle_data(cls):
        tackle_path = DATA_PATH / "tackle"
        tackle_files = os.listdir(tackle_path)
        for tackle_file in tackle_files:
            tackle_data = yaml.safe_load((tackle_path / tackle_file).read_bytes())
            if tackle_data is None:
                continue
            for key in tackle_data:
                try:
                    tackle = FishingTackle(tackle_data[key])
                    fishing_tackle_dict[tackle.name] = tackle
                    item = Item(tackle.name, ItemType.PROP, tackle.description, 0)
                    items.register(item)
                    cls.fishing_shop.add_goods(item, tackle_data[key]['price'])
                except Exception as e:
                    log.error(f'trackle-{tackle_file}-{key}无效渔具配置:\n{str_traceback(e)}')

    @classmethod
    def load_treasure_data(cls):
        treasure_path = DATA_PATH / "treasure"
        treasure_files = os.listdir(treasure_path)
        for treasure_file in treasure_files:
            treasure_data = yaml.safe_load((treasure_path / treasure_file).read_bytes())
            if treasure_data is None:
                continue
            for key in treasure_data:
                try:
                    treasure_manager.add_dict_loot(key, treasure_data[key])
                except Exception as e:
                    log.error(f'treasure-{treasure_file}-{key}无效宝藏配置:\n{str_traceback(e)}')


@item_loading_events.handle()
def loading_lkfishing_data():
    FishingController.fish_area_data.clear()
    fishing_rod_dict.clear()
    bait_dict.clear()
    treasure_manager.clear()
    FishingController.fishing_shop.clear_goods()
    fishing_tackle_dict.clear()
    achievements.clear()
    FishingController.load_fish_data()
    FishingController.load_fishing_rod_data()
    FishingController.load_bait_data()
    FishingController.load_fishing_tackle_data()
    FishingController.load_treasure_data()
    shops.register(FishingController.fishing_shop)
    load_achievements()
    log.success(f'lkfishing数据加载完成')
