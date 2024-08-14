from ATRI.database.sqlite import MySQLite
from ATRI.plugins.lkbot.util import lk_util, DB_NAME
from ATRI.plugins.lkbot.system.data.user import Users, users

from .pet_chat import PetModel


class PetData:
    def __init__(self, data, data_type):
        if data_type == 0:
            self.id = data[0]
            self.name = data[1]
            self.instruction = data[2]
            self.love = data[3]
        elif data_type == 1:
            self.id = data[0]
            self.name = data[1]
            self.instruction = data[2]
            self.love = 0


class PetManager:
    def __init__(self):
        self.convos = dict()
        self.datas = dict()
        self.sql = MySQLite(DB_NAME, "PETDATA")
        self.sql.create_table('''
ID          INTEGER PRIMARY KEY,
NAME        TEXT NOT NULL,
INSTRUCTION TEXT NOT NULL,
LOVE        INT DEFAULT 0
'''
                              )
        content = self.sql.read_all()
        for row in content:
            self.datas[str(row[0])] = PetData(row, 0)
            self.convos[str(row[0])] = PetModel(lk_util.get_name(str(row[0])), row[1], row[2])
            users.userdata[str(row[0])].petname = row[1]
        Users.user_name_changed_event.subscribe(self.change_user_name)

    def new_pet(self, data: list):
        self.datas[data[0]] = PetData(data, 1)
        self.convos[data[0]] = PetModel(lk_util.get_name(data[0]), data[1], data[2])
        users.userdata[data[0]].petname = data[1]
        self.sql.insert('ID, NAME, INSTRUCTION', f"{data[0]}, '{data[1]}', '{data[2]}'")

    def change_pet_name(self, user_id, pet_name):
        self.datas[user_id].name = pet_name
        self.convos[user_id].change_pet_name(pet_name)
        users.userdata[user_id].petname = pet_name
        self.sql.update(f"NAME = '{pet_name}'", f"ID={user_id}")

    def change_pet_inst(self, user_id, inst):
        self.datas[user_id].instruction = inst
        self.convos[user_id].change_instruction(inst)
        self.sql.update(f"INSTRUCTION = '{inst}'", f"ID={user_id}")

    def change_user_name(self, user_id, new_name):
        self.convos[user_id].change_user_name(new_name)
