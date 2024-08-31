from pathlib import Path
from tortoise import Tortoise

from ATRI.log import log

# 临时的实现，寻求更好的方式！欢迎pr


DB_DIR = Path(".") / "data" / "sql"
DB_DIR.mkdir(parents=True, exist_ok=True)


async def run():
    from ATRI.database import models

    await Tortoise.init(
        {
            "connections": {
                "bilibili": {
                    "engine": "tortoise.backends.sqlite",
                    "credentials": {"file_path": f"{DB_DIR}/bilibili.sqlite3"},
                },
                "ts": {
                    "engine": "tortoise.backends.sqlite",
                    "credentials": {"file_path": f"{DB_DIR}/thesaurusstoragor.sqlite3"},
                },
                "tal": {
                    "engine": "tortoise.backends.sqlite",
                    "credentials": {
                        "file_path": f"{DB_DIR}/thesaurusauditlist.sqlite3"
                    },
                },
                "rrs": {
                    "engine": "tortoise.backends.sqlite",
                    "credentials": {
                        "file_path": f"{DB_DIR}/rssrsshubsubscription.sqlite3"
                    },
                },
                "rms": {
                    "engine": "tortoise.backends.sqlite",
                    "credentials": {
                        "file_path": f"{DB_DIR}/rssmikananisubscription.sqlite3"
                    },
                },
            },
            "apps": {
                "bilibili": {
                    "models": [locals()["models"]],
                    "default_connection": "bilibili",
                },
                "ts": {"models": [locals()["models"]], "default_connection": "ts"},
                "tal": {"models": [locals()["models"]], "default_connection": "tal"},
                "rrs": {"models": [locals()["models"]], "default_connection": "rrs"},
                "rms": {"models": [locals()["models"]], "default_connection": "rms"},
            },
        }
    )
    await Tortoise.generate_schemas()


async def init_database():
    log.info("正在初始化数据库...")
    await run()
    log.success("数据库初始化完成")


async def close_database_connection():
    log.info("正在关闭数据库连接...")
    await Tortoise.close_connections()
    log.info("数据库成功关闭")
