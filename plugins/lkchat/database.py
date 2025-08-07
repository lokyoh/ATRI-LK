from ATRI.utils.sqlite import DataBase

atri_db = DataBase("atri.db")

user_table = atri_db.get_table("USERDATA", '''
ID      INTEGER PRIMARY KEY,
DATA    TEXT    DEFAULT '{}'
''', 0, None)

word_table = atri_db.get_table("WORD", '''
WORD    TEXT    DEFAULT '',
MEANING TEXT    DEFAULT '',
IMPORT  INTEGER    DEFAULT 0
''', 0, None)
