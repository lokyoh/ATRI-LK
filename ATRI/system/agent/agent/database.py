import atexit

from ATRI.utils.sqlite import DataBase

atri_db = DataBase("atri.db")
atexit.register(atri_db.disconnect)


def update_user_db(connection, version):
    cursor = connection.cursor()
    if version < 1:
        cursor.execute("DROP TABLE IF EXISTS `USERDATA_TEMP`")
        cursor.execute(
            "CREATE TABLE `USERDATA_TEMP` (ID TEXT PRIMARY KEY, DATA TEXT DEFAULT '{}')"
        )
        cursor.execute(
            """
            INSERT INTO `USERDATA_TEMP` (`ID`, `DATA`)
            SELECT CAST(ID AS TEXT), `DATA` FROM `USERDATA`
            """
        )
        cursor.execute("DROP TABLE `USERDATA`")
        cursor.execute("ALTER TABLE `USERDATA_TEMP` RENAME TO `USERDATA`")
    cursor.close()


user_table = atri_db.get_table(
    "USERDATA",
    """
ID      TEXT PRIMARY KEY,
DATA    TEXT    DEFAULT '{}'
""",
    1,
    update_user_db,
)

word_table = atri_db.get_table(
    "WORD",
    """
WORD    TEXT    DEFAULT '',
MEANING TEXT    DEFAULT '',
IMPORT  INTEGER    DEFAULT 0
""",
    0,
    None,
)

img_table = atri_db.get_table(
    "IMAGE",
    """
ID      INTEGER PRIMARY KEY,
FILE_NAME TEXT    DEFAULT '',
HASH    TEXT    DEFAULT '',
FILE_SIZE INTEGER    DEFAULT 0,
DESP    TEXT    DEFAULT '',
UPDATE_AT REAL NOT NULL
""",
    0,
    None,
)
