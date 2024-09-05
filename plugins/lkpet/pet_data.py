from threading import Lock

from ATRI.utils.sqlite import encode, decode
from ATRI.system.lkbot.data.user import Users, users, lk_db

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
        self.lock = dict()
        table_content = '''
ID          INTEGER PRIMARY KEY,
NAME        TEXT NOT NULL,
INSTRUCTION TEXT NOT NULL,
LOVE        INT DEFAULT 0
'''

        def update_db(connection, version):
            cursor = connection.cursor()
            if version < 1:
                pass
            cursor.close()

        self.sql = lk_db.get_table("PETDATA", table_content, 0, update_db)
        content = self.sql.select_all("ID, NAME, INSTRUCTION, LOVE")
        for row in content:
            user_id = str(row[0])
            self.datas[user_id] = PetData(row, 0)
            self.convos[user_id] = PetModel(users.get_user_name(user_id), row[1], decode(row[2]))
            self.lock[user_id] = Lock()
            users.petname_set(user_id, row[1])

    def new_pet(self, user_id, name, instruction):
        self.lock[user_id] = Lock()
        self.lock[user_id].acquire()
        self.datas[user_id] = PetData([user_id, name, instruction], 1)
        self.convos[user_id] = PetModel(users.get_user_name(user_id), name, instruction)
        users.petname_set(user_id, name)
        self.sql.insert('ID, NAME, INSTRUCTION', f"{user_id}, '{name}', '{encode(instruction)}'")
        self.lock[user_id].release()

    def change_pet_name(self, user_id, pet_name):
        self.lock[user_id].acquire()
        self.datas[user_id].name = pet_name
        self.convos[user_id].change_pet_name(pet_name)
        users.petname_set(user_id, pet_name)
        self.sql.update(f"NAME = '{pet_name}'", f"ID={user_id}")
        self.lock[user_id].release()

    def change_pet_inst(self, user_id, inst):
        self.lock[user_id].acquire()
        self.datas[user_id].instruction = inst
        self.convos[user_id].change_instruction(inst)
        self.sql.update(f"INSTRUCTION = '{encode(inst)}'", f"ID={user_id}")
        self.lock[user_id].release()


pet_manager = PetManager()


@Users.user_name_changed_event.handle()
def _(user_id, new_name):
    pet_manager.convos[user_id].change_user_name(new_name)
