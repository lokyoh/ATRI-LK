import json

from ATRI.utils.lock import GroupLock
from ATRI.system.lkapi.entity.item import ToolItemStack
from ATRI.system.lkapi.entity.user import get_user_data, BackPack

from .fishing_rod import fishing_rod_dict
from .bait import bait_dict
from .fishing_tackle import fishing_tackle_dict
from .exception import NoFishingRod, NoBait, FishingException
from .achievement import check_achievement

from ..database import fishing_table, User, AchievementData

fishing_user_lock = GroupLock()


class FishingUser:
    def __init__(self, user_id, data: User, achievement: AchievementData):
        self.user_id = user_id
        self.fishing_rod = None
        with get_user_data(self.user_id) as user_data:
            if not data.fishing_rod:
                pass
            elif data.fishing_rod in fishing_rod_dict:
                self.fishing_rod = fishing_rod_dict[data.fishing_rod]
            else:
                self.remove_rod(user_data)
            self.fishing_rod_damage = data.fishing_rod_damage
            self.bait = None
            if not data.bait:
                pass
            elif data.bait in bait_dict:
                self.bait = bait_dict[data.bait]
            else:
                self.remove_bait(user_data)
            self.bait_num = data.bait_num
            self.fishing_tackle = None
            if not data.fishing_tackle:
                pass
            elif data.fishing_tackle in fishing_tackle_dict:
                self.fishing_tackle = fishing_tackle_dict[data.fishing_tackle]
            else:
                self.remove_tackle(user_data)
        self.fishing_tackle_damage = data.fishing_tackle_damage
        self.position = data.position
        self.xp = data.xp
        self.level = data.level
        self.achievement = achievement
        self._modify = False

    def __str__(self):
        p = None
        if self.position == 'lake':
            p = '湖'
        elif self.position == 'river':
            p = '河'
        elif self.position == 'ocean':
            p = '海'
        return (
            f'钓鱼等级:{self.level} 经验:{self.xp}\n'
            f'当前位置:{p}\n'
            f'装备:\n'
            f'鱼竿:{'未装备' if self.fishing_rod is None or self.fishing_rod.durable <= self.fishing_rod_damage else
            f'{self.fishing_rod.name} [{self.fishing_rod.durable - self.fishing_rod_damage}/{self.fishing_rod.durable}]'}\n'
            f'鱼饵:{'未装备' if self.bait is None or self.bait_num <= 0 else
            f'{self.bait.name}*{self.bait_num}'}\n'
            f'鱼具:{'未装备' if self.fishing_tackle is None or self.fishing_tackle.durable <= self.fishing_tackle_damage else
            f'{self.fishing_tackle.name} [{self.fishing_tackle.durable - self.fishing_tackle_damage}/{self.fishing_tackle.durable}]'}'
        )

    def get_fishing_rod(self):
        if self.fishing_rod is None or self.fishing_rod.durable <= self.fishing_rod_damage:
            self.fishing_rod = None
            self.fishing_rod_damage = 0
            raise NoFishingRod()
        return self.fishing_rod

    def get_bait(self):
        if self.bait is None or self.bait_num <= 0:
            self.bait = None
            self.bait_num = 0
            raise NoBait()
        return self.bait

    def get_fishing_tackle(self):
        if self.fishing_tackle is None or self.fishing_tackle.durable <= self.fishing_tackle_damage:
            self.fishing_tackle = None
            self.fishing_tackle_damage = 0
            return None
        return self.fishing_tackle

    def increase_damage(self):
        self.fishing_rod_damage += 1
        if self.fishing_rod.durable <= self.fishing_rod_damage:
            self.fishing_rod = None
            self.fishing_rod_damage = 0

    def use_bait(self):
        self.bait_num -= 1
        if self.bait_num <= 0:
            self.bait = None
            self.bait_num = 0

    def increase_tackle_damage(self):
        self.fishing_tackle_damage += 1
        if self.fishing_tackle.durable <= self.fishing_tackle_damage:
            self.fishing_tackle = None
            self.fishing_tackle_damage = 0

    def add_xp(self, xp: int):
        self.xp += xp
        while self.xp >= (self.level + 1) * 1000:
            self.xp -= (self.level + 1) * 1000
            self.level += 1

    def remove_rod(self, user_data):
        if self.fishing_rod and self.fishing_rod.durable > self.fishing_rod_damage:
            bp = user_data.backpack
            bp.set_item_with_stack(
                ToolItemStack(bp.get_item_stack(self.fishing_rod.name), self.fishing_rod.durable)
                .add_used_tool(self.fishing_rod_damage)
            )
            self.fishing_rod = None
            self.fishing_rod_damage = 0

    def remove_bait(self, user_data):
        if self.bait and self.bait_num > 0:
            user_data.item_num_change(self.bait.name, self.bait_num)
            self.bait = None
            self.bait_num = 0

    def remove_tackle(self, user_data):
        if self.fishing_tackle and self.fishing_tackle.durable > self.fishing_tackle_damage:
            bp = user_data.backpack
            bp.set_item_with_stack(
                ToolItemStack(bp.get_item_stack(self.fishing_tackle.name), self.fishing_tackle.durable)
                .add_used_tool(self.fishing_tackle_damage)
            )
            self.fishing_tackle = None
            self.fishing_tackle_damage = 0

    def equip_rod(self, name, user_data) -> bool:
        if name not in fishing_rod_dict:
            return False
        bp: BackPack = user_data.backpack
        if not bp.bp_has_item(name):
            return False
        self.remove_rod(user_data)
        rod = fishing_rod_dict[name]
        stack = ToolItemStack(bp.get_item_stack(name), rod.durable)
        damage = stack.get_used_tool()
        bp.set_item_with_stack(stack)
        if damage is None:
            return False
        self.fishing_rod = rod
        self.fishing_rod_damage = damage
        return True

    def equip_bait(self, name, user_data) -> bool:
        if name not in bait_dict:
            return False
        bp: BackPack = user_data.backpack
        if not bp.bp_has_item(name):
            return False
        self.remove_bait(user_data)
        self.bait = bait_dict[name]
        self.bait_num = bp.get_item_stack(name).meta.num
        bp.remove_item(name)
        return True

    def equip_tackle(self, name, user_data) -> bool:
        if name not in fishing_tackle_dict:
            return False
        bp: BackPack = user_data.backpack
        if not bp.bp_has_item(name):
            return False
        self.remove_tackle(user_data)
        tackle = fishing_tackle_dict[name]
        stack = ToolItemStack(bp.get_item_stack(name), tackle.durable)
        damage = stack.get_used_tool()
        bp.set_item_with_stack(stack)
        self.fishing_tackle = fishing_tackle_dict[name]
        self.fishing_tackle_damage = damage
        return True

    def add_fish(self, fish_data) -> list:
        self._modify = True
        msg = []
        name = fish_data.fish.name
        data = self.achievement.fish_data
        if name not in data:
            data[name] = {}
        data[name]['times'] = data[name].get('times', 0) + 1
        if (not data[name].get('max', False)) and fish_data.length and fish_data.length > data[name].get('length', 0):
            data[name]['length'] = fish_data.length
            if fish_data.length == fish_data.fish.size['max']:
                data[name]['max'] = True
        self.achievement.achievement_data['times'] = self.achievement.achievement_data.get('times', 0) + 1
        check_achievement(self.achievement, fish_data, msg)
        return msg

    def to_dict(self):
        return {
            'user_id': self.user_id,
            'fishing_rod': f'{self.fishing_rod.name}' if self.fishing_rod is not None else '',
            'fishing_rod_damage': self.fishing_rod_damage,
            'bait': f'{self.bait.name}' if self.bait is not None else '',
            'bait_num': self.bait_num,
            'fishing_tackle': f'{self.fishing_tackle}' if self.fishing_tackle is not None else '',
            'fishing_tackle_damage': self.fishing_tackle_damage,
            'position': self.position,
            'xp': self.xp,
            'level': self.level
        }

    def save_data(self):
        fishing_table.update(
            (('DATA', 'ACHI'), (json.dumps(self.to_dict(), ensure_ascii=False), self.achievement.model_dump_json()),),
            f"ID={self.user_id}")

    def __setattr__(self, key, value):
        if not key.startswith('_'):
            self._modify = True
        super().__setattr__(key, value)

    def __enter__(self):
        fishing_user_lock[self.user_id].acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if (exc_type is None or exc_type is FishingException) and self._modify:
            self.save_data()
        fishing_user_lock[self.user_id].release()
        return False


def get_fish_user_data(user_id: int) -> FishingUser:
    data = fishing_table.select("DATA, ACHI", f"ID={user_id}")
    if len(data) == 0:
        user = User()
        achi = AchievementData()
        fishing_table.insert("ID, DATA, ACHI", (int(user_id), user.model_dump_json(), achi.model_dump_json()))
        user_info = FishingUser(user_id, user, achi)
    else:
        user_info = FishingUser(user_id, User.model_validate_json(data[0][0]),
                                AchievementData.model_validate_json(data[0][1]))
    return user_info
