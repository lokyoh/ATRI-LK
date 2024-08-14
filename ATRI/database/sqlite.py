import sqlite3

from ATRI.database.db import DB_DIR


class MySQLite:
    def __init__(self, db, table_name):
        self.conn = sqlite3.connect(f'{DB_DIR}/{db}')
        self.c = self.conn.cursor()
        self.table_name = table_name

    def create_table(self, table_content: str):
        self.c.execute(f'''CREATE TABLE IF NOT EXISTS {self.table_name} ( {table_content} );''')
        self.conn.commit()

    def read_all(self, content='*'):
        cursor = self.c.execute(f'''SELECT {content} FROM {self.table_name}''')
        content = []
        for row in cursor:
            content.append(row)
        return content

    def insert(self, content, value):
        self.c.execute(f"INSERT INTO {self.table_name} ({content}) \
      VALUES ({value})")
        self.conn.commit()

    def update(self, content, req):
        self.c.execute(f"UPDATE {self.table_name} set {content} where {req}")
        self.conn.commit()

    def delete(self, req):
        self.c.execute(f"DELETE from {self.table_name} where {req}")
        self.conn.commit()
