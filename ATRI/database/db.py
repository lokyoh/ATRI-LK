from tortoise import Tortoise

from ATRI.log import log
from ATRI.dir import DB_DIR
from ATRI.bot.statistics import model as statistics_model

# 临时的实现，寻求更好的方式！欢迎pr


data = {}


def add_database(name: str, model):
    if name not in data:
        data[name] = [model]
    else:
        data[name].append(model)


async def run():
    database = {
        "connections": {},
        "apps": {}
    }
    for d in data:
        database["connections"][d] = {
            "engine": "tortoise.backends.sqlite",
            "credentials": {
                "file_path": f"{DB_DIR}/{d}.sqlite3",
            }
        }
        database["apps"][d] = {
            "models": data[d],
            "default_connection": d,
        }
    await Tortoise.init(database)
    await Tortoise.generate_schemas()


async def init_database():
    add_database("ATRI", statistics_model)
    log.info(f"正在初始化{len(data)}个数据库...")
    await run()
    log.success("数据库初始化完成")


async def close_database_connection():
    log.info("正在关闭数据库连接...")
    await Tortoise.close_connections()
    log.info("数据库成功关闭")
