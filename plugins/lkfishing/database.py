from ATRI.utils.sqlite import DBTable
from ATRI.system.lkapi.bot import db as lk_db
from ATRI.utils.model import BaseModel


class User(BaseModel):
    fishing_rod: str = ''
    fishing_rod_damage: int = 0
    bait: str = ''
    bait_num: int = 0
    fishing_tackle: str = ''
    fishing_tackle_damage: int = 0
    position: str = 'lake'
    xp: int = 0
    level: int = 0


class AchievementData(BaseModel):
    fish_data: dict = {}
    achieve: list = []
    achievement_data: dict = {}


table_content = '''
ID      INTEGER PRIMARY KEY,
DATA    TEXT    DEFAULT '{}',
ACHI    TEXT    DEFAULT '{}'
'''

fishing_table: DBTable = lk_db.get_table('LKFISHING', table_content, 0, None)
