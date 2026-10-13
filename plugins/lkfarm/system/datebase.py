from ATRI.system.lkapi.bot import db as lk_db
from ATRI.utils.sqlite import DBTable

farm_table_content = """
DATE            TEXT    DEFAULT '2000-1-1',
WEATHER         INTEGER DEFAULT 0,
NEXT_WEATHER    INTEGER DEFAULT 0
"""


def update_db(connection, version):
    cursor = connection.cursor()
    if version < 1:
        pass
    cursor.close()


farm_table: DBTable = lk_db.get_table("LKFARM", farm_table_content, 0, update_db)

user_table_content = """
ID          TEXT    PRIMARY KEY,
DATE        TEXT    DEFAULT '2000-01-01',
ENDURANCE   INTEGER DEFAULT 1500,
LUCKY       INTEGER DEFAULT 50,
EXP         INTEGER DEFAULT 0,
FIELD_A1    TEXT    DEFAULT '{}',
FIELD_A2    TEXT    DEFAULT '{}',
FIELD_A3    TEXT    DEFAULT '{}',
FIELD_A4    TEXT    DEFAULT '{}',
FIELD_A5    TEXT    DEFAULT '{}',
FIELD_A6    TEXT    DEFAULT '{}',
FIELD_A7    TEXT    DEFAULT '{}',
FIELD_A8    TEXT    DEFAULT '{}',
FIELD_B1    TEXT    DEFAULT '{}',
FIELD_B2    TEXT    DEFAULT '{}',
FIELD_B3    TEXT    DEFAULT '{}',
FIELD_B4    TEXT    DEFAULT '{}',
FIELD_B5    TEXT    DEFAULT '{}',
FIELD_B6    TEXT    DEFAULT '{}',
FIELD_B7    TEXT    DEFAULT '{}',
FIELD_B8    TEXT    DEFAULT '{}',
FIELD_C1    TEXT    DEFAULT '{}',
FIELD_C2    TEXT    DEFAULT '{}',
FIELD_C3    TEXT    DEFAULT '{}',
FIELD_C4    TEXT    DEFAULT '{}',
FIELD_C5    TEXT    DEFAULT '{}',
FIELD_C6    TEXT    DEFAULT '{}',
FIELD_C7    TEXT    DEFAULT '{}',
FIELD_C8    TEXT    DEFAULT '{}',
FIELD_D1    TEXT    DEFAULT '{}',
FIELD_D2    TEXT    DEFAULT '{}',
FIELD_D3    TEXT    DEFAULT '{}',
FIELD_D4    TEXT    DEFAULT '{}',
FIELD_D5    TEXT    DEFAULT '{}',
FIELD_D6    TEXT    DEFAULT '{}',
FIELD_D7    TEXT    DEFAULT '{}',
FIELD_D8    TEXT    DEFAULT '{}'
"""


def update_user_db(connection, version):
    cursor = connection.cursor()
    if version < 1:
        cursor.execute("DROP TABLE IF EXISTS `LKFARMUSERDATA_TEMP`")
        cursor.execute(f"CREATE TABLE `LKFARMUSERDATA_TEMP` ({user_table_content})")
        cursor.execute(
            """
            INSERT INTO `LKFARMUSERDATA_TEMP` (
            `ID`, `DATE`, `ENDURANCE`, `LUCKY`, `EXP`, `FIELD_A1`, `FIELD_A2`, `FIELD_A3`,
            `FIELD_A4`, `FIELD_A5`, `FIELD_A6`, `FIELD_A7`, `FIELD_A8`, `FIELD_B1`, `FIELD_B2`,
            `FIELD_B3`, `FIELD_B4`, `FIELD_B5`, `FIELD_B6`, `FIELD_B7`, `FIELD_B8`, `FIELD_C1`,
            `FIELD_C2`, `FIELD_C3`, `FIELD_C4`, `FIELD_C5`, `FIELD_C6`, `FIELD_C7`, `FIELD_C8`,
            `FIELD_D1`, `FIELD_D2`, `FIELD_D3`, `FIELD_D4`, `FIELD_D5`, `FIELD_D6`, `FIELD_D7`, `FIELD_D8`)
            SELECT CAST(ID AS TEXT), `DATE`, `ENDURANCE`, `LUCKY`, `EXP`, `FIELD_A1`, `FIELD_A2`, `FIELD_A3`,
            `FIELD_A4`, `FIELD_A5`, `FIELD_A6`, `FIELD_A7`, `FIELD_A8`, `FIELD_B1`, `FIELD_B2`, `FIELD_B3`,
            `FIELD_B4`, `FIELD_B5`, `FIELD_B6`, `FIELD_B7`, `FIELD_B8`, `FIELD_C1`, `FIELD_C2`, `FIELD_C3`,
            `FIELD_C4`, `FIELD_C5`, `FIELD_C6`, `FIELD_C7`, `FIELD_C8`, `FIELD_D1`, `FIELD_D2`, `FIELD_D3`,
            `FIELD_D4`, `FIELD_D5`, `FIELD_D6`, `FIELD_D7`, `FIELD_D8` FROM `LKFARMUSERDATA`
            """
        )
        cursor.execute("DROP TABLE `LKFARMUSERDATA`")
        cursor.execute("ALTER TABLE `LKFARMUSERDATA_TEMP` RENAME TO `LKFARMUSERDATA`")
    cursor.close()


user_table: DBTable = lk_db.get_table(
    "LKFARMUSERDATA", user_table_content, 1, update_user_db
)
