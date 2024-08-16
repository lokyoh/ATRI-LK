import sqlite3

from ATRI.database.db import DB_DIR


class DataBase:
    def __init__(self, database_name: str, db_version: int, update_dp):
        self.connection = sqlite3.connect(f"{DB_DIR}/{database_name}")
        cursor = self.connection.cursor()
        cursor.execute(f"CREATE TABLE IF NOT EXISTS VERSION ( VERSION INTEGER DEFAULT {db_version} );")
        self.connection.commit()
        cursor.execute(f"SELECT VERSION FROM VERSION")
        content = []
        for row in cursor:
            content.append(row)
        if len(content) == 0:
            cursor.execute(f"INSERT INTO VERSION (VERSION) VALUES ({db_version})")
            self.connection.commit()
        else:
            now_version = content[0][0]
            if now_version != db_version:
                if update_dp is not None:
                    update_dp(self.connection, now_version)
                cursor.execute(f"UPDATE VERSION SET VERSION = {db_version} WHERE VERSION = {now_version}")
                self.connection.commit()
        cursor.close()


class DBTable:
    def __init__(self, db: DataBase, table_name, table_content):
        self._conn = db.connection
        self._c = self._conn.cursor()
        self.table_name = table_name
        self._c.execute(f'''CREATE TABLE IF NOT EXISTS {self.table_name} ( {table_content} );''')
        self._conn.commit()

    def select_all(self, content='*'):
        cursor = self._c.execute(f'''SELECT {content} FROM {self.table_name}''')
        content = []
        for row in cursor:
            content.append(row)
        return content

    def select(self, content, req):
        cursor = self._c.execute(f"SELECT {content} FROM {self.table_name} WHERE {req}")
        content = []
        for row in cursor:
            content.append(row)
        return content

    def insert(self, content, value):
        self._c.execute(f"INSERT INTO {self.table_name} ({content}) VALUES ({value})")
        self._conn.commit()

    def update(self, content, req):
        self._c.execute(f"UPDATE {self.table_name} SET {content} WHERE {req}")
        self._conn.commit()

    def delete(self, req):
        self._c.execute(f"DELETE FROM {self.table_name} WHERE {req}")
        self._conn.commit()
