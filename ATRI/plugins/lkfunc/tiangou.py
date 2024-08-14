import random

from ATRI import driver, TEXT_DIR
from ATRI.log import log

DATA_PATH = TEXT_DIR / "tiangou.txt"


class Tiangou:
    tiangou = []

    def __init__(self):
        with open(DATA_PATH, 'r', encoding='utf-8') as file:
            for line in file:
                self.tiangou.append(line)

    def get_tiangou(self):
        return random.choice(self.tiangou)


tiangou = Tiangou()

driver().on_startup(lambda: log.success(f'舔狗日记共加载{len(Tiangou.tiangou)}个'))
