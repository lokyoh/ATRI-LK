from sqlite3 import Connection, connect

from ATRI.database.db import DB_DIR


class DBTable:
    def __init__(self, connection: Connection, table_name):
        self._conn = connection
        self.table_name = table_name

    def select_all(self, content='*'):
        cursor = self._conn.cursor()
        result = cursor.execute(f'''SELECT {content} FROM {self.table_name}''')
        content = []
        for row in result:
            content.append(row)
        cursor.close()
        return content

    def select(self, content, req):
        cursor = self._conn.cursor()
        result = cursor.execute(f"SELECT {content} FROM {self.table_name} WHERE {req}")
        content = []
        for row in result:
            content.append(row)
        cursor.close()
        return content

    def insert(self, content, value):
        cursor = self._conn.cursor()
        cursor.execute(f"INSERT INTO {self.table_name} ({content}) VALUES ({value})")
        self._conn.commit()
        cursor.close()

    def update(self, content, req):
        cursor = self._conn.cursor()
        cursor.execute(f"UPDATE {self.table_name} SET {content} WHERE {req}")
        self._conn.commit()
        cursor.close()

    def delete(self, req):
        cursor = self._conn.cursor()
        cursor.execute(f"DELETE FROM {self.table_name} WHERE {req}")
        self._conn.commit()
        cursor.close()


class DataBase:
    def __init__(self, database_name: str):
        self._connection = connect(f"{DB_DIR}/{database_name}")
        self._table_list = []
        cursor = self._connection.cursor()
        cursor.execute(f"CREATE TABLE IF NOT EXISTS TABLEVERSION ( TABLENAME TEXT, VERSION INTEGER );")
        self._connection.commit()
        cursor.close()

    def get_table(self, table_name: str, table_content: str, table_version: int, update_dp) -> DBTable:
        if table_name in self._table_list:
            raise ValueError(f"表 {table_name} 已经存在")
        self._table_list.append(table_name)
        cursor = self._connection.cursor()
        cursor.execute(f'''CREATE TABLE IF NOT EXISTS {table_name} ( {table_content} );''')
        self._connection.commit()
        cursor.execute(f"SELECT VERSION FROM TABLEVERSION WHERE TABLENAME = '{table_name}'")
        result = []
        for row in cursor:
            result.append(row)
        if len(result) > 0:
            now_version = result[0][0]
            if now_version != table_version:
                if update_dp is not None:
                    update_dp(self._connection, now_version)
                cursor.execute(f"UPDATE TABLEVERSION SET VERSION = {table_version} WHERE TABLENAME = '{table_name}'")
                self._connection.commit()
        else:
            cursor.execute(f"INSERT INTO TABLEVERSION (TABLENAME, VERSION) VALUES ('{table_name}', {table_version})")
            self._connection.commit()
        cursor.close()
        return DBTable(self._connection, table_name)

    def get_exist_table(self, table_name: str):
        return DBTable(self._connection, table_name)

    def disconnect(self):
        self._connection.close()


def encode(value: str) -> str:
    value = value.replace("%", "%0")
    return value.replace("`", "%1").replace("'", "%2").replace("\"", "%3").replace(" ", "%4")


def decode(value: str) -> str:
    value = value.replace("%1", "`").replace("%2", "'").replace("%3", "\"").replace("%4", " ")
    return value.replace("%0", "%")
