from ATRI.system.lkapi.bot import db as lk_db


def update_feed_db(connection, version):
    cursor = connection.cursor()
    if version < 1:
        cursor.execute("DROP TABLE IF EXISTS `LKFISHING_TEMP`")
        cursor.execute(
            "CREATE TABLE `LKFISHING_TEMP` (ID TEXT PRIMARY KEY, DATE TEXT DEFAULT '2000-01-01')"
        )
        cursor.execute(
            """
            INSERT INTO `LKFISHING_TEMP` (`ID`, `DATE`)
            SELECT CAST(ID AS TEXT), `DATE` FROM `LKFISHING`
            """
        )
        cursor.execute("DROP TABLE `LKFISHING`")
        cursor.execute("ALTER TABLE `LKFISHING_TEMP` RENAME TO `LKFISHING`")
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
