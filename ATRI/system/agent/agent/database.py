import atexit

from ATRI.utils.sqlite import DataBase

atri_db = DataBase("atri.db")
atexit.register(atri_db.disconnect)

user_table = atri_db.get_table("USERDATA", '''
ID      INTEGER PRIMARY KEY,
DATA    TEXT    DEFAULT '{}'
''', 0, None)

word_table = atri_db.get_table("WORD", '''
WORD    TEXT    DEFAULT '',
MEANING TEXT    DEFAULT '',
IMPORT  INTEGER    DEFAULT 0
''', 0, None)

img_table = atri_db.get_table("IMAGE", '''
ID      INTEGER PRIMARY KEY,
FILE_NAME TEXT    DEFAULT '',
HASH    TEXT    DEFAULT '',
FILE_SIZE INTEGER    DEFAULT 0,
DESP    TEXT    DEFAULT '',
UPDATE_AT REAL NOT NULL
''', 0, None)
