from ATRI.system.lkapi.bot import db as lk_db


def update_feed_db(connection, version):
    cursor = connection.cursor()
    if version < 1:
        cursor.execute("DROP TABLE IF EXISTS `LKFEEDDATA_TEMP`")
        cursor.execute(
            "CREATE TABLE `LKFEEDDATA_TEMP` (ID TEXT PRIMARY KEY, DATE TEXT DEFAULT '2000-01-01')"
        )
        cursor.execute(
            """
            INSERT INTO `LKFEEDDATA_TEMP` (`ID`, `DATE`)
            SELECT CAST(ID AS TEXT), `DATE` FROM `LKFEEDDATA`
            """
        )
        cursor.execute("DROP TABLE `LKFEEDDATA`")
        cursor.execute("ALTER TABLE `LKFEEDDATA_TEMP` RENAME TO `LKFEEDDATA`")
    cursor.close()


feed_db = lk_db.get_table(
    "LKFEEDDATA",
    """
    ID          TEXT    PRIMARY KEY,
    DATE        TEXT    DEFAULT '2000-01-01'
    """,
    1,
    update_feed_db,
)
