from typing import ClassVar

from ATRI.system.lkapi.bot import db as lk_db
from ATRI.utils.model import BaseModel
from ATRI.utils.sqlite import DBTable


class User(BaseModel):
    fishing_rod: str = ""
    fishing_rod_damage: int = 0
    bait: str = ""
    bait_num: int = 0
    fishing_tackle: str = ""
    fishing_tackle_damage: int = 0
    position: str = "lake"
    xp: int = 0
    level: int = 0


class AchievementData(BaseModel):
    fish_data: ClassVar[dict] = {}
    achieve: ClassVar[list] = []
    achievement_data: ClassVar[dict] = {}


table_content = """
ID      TEXT PRIMARY KEY,
DATA    TEXT    DEFAULT '{}',
ACHI    TEXT    DEFAULT '{}'
"""


def update_user_db(connection, version):
    cursor = connection.cursor()
    if version < 1:
        cursor.execute("DROP TABLE IF EXISTS `LKFISHING_TEMP`")
        cursor.execute(f"CREATE TABLE `LKFISHING_TEMP` ({table_content})")
        cursor.execute(
            """
            INSERT INTO `LKFISHING_TEMP` (`ID`, `DATA`, `ACHI`)
            SELECT CAST(ID AS TEXT), `DATA`, `ACHI` FROM `LKFISHING`
            """
        )
        cursor.execute("DROP TABLE `LKFISHING`")
        cursor.execute("ALTER TABLE `LKFISHING_TEMP` RENAME TO `LKFISHING`")
    cursor.close()


fishing_table: DBTable = lk_db.get_table("LKFISHING", table_content, 1, update_user_db)
