import time

from ATRI.utils.datetime import fromtimestamp
from ATRI.utils.model import BaseModel
from ATRI.utils.sqlite import DataBase


class TokenUsage(BaseModel):
    prompt_tokens: int
    output_tokens: int
    cache_hit_tokens: int

    def __str__(self):
        return f"输入={self.prompt_tokens}, 输出={self.output_tokens}, 缓存命中={self.cache_hit_tokens}"


class TokenUsageData(BaseModel):
    name: str
    event: str
    usage: TokenUsage
    time: str
    """记录时间格式:yyyy年mm月dd日 hh:mm:ss"""


class ModelTokenUsageManager:
    def __init__(self, db_name: str = "agent.db", tb_name: str = "tokens"):
        self._db = DataBase(db_name)
        table_content = (
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " model_name TEXT NOT NULL,"
            " event TEXT NOT NULL,"
            " prompt_tokens INT NOT NULL,"
            " output_tokens INT NOT NULL,"
            " cache_hit_tokens INT NOT NULL,"
            " created_at REAL NOT NULL"
        )
        self._table = self._db.get_table(tb_name, table_content, 0)

    def add_usage(self, model_name: str, event: str, usage: TokenUsage | dict):
        if isinstance(usage, dict):
            usage_obj = TokenUsage.model_validate(usage)
        else:
            usage_obj = usage

        self._table.insert(
            {
                "model_name": model_name,
                "event": event,
                "prompt_tokens": int(usage_obj.prompt_tokens),
                "output_tokens": int(usage_obj.output_tokens),
                "cache_hit_tokens": int(usage_obj.cache_hit_tokens),
                "created_at": time.time(),
            }
        )

    @staticmethod
    def _to_usage_data(row) -> TokenUsageData:
        (
            model_name,
            event,
            prompt_tokens,
            output_tokens,
            cache_hit_tokens,
            created_at,
        ) = row
        return TokenUsageData(
            name=model_name,
            event=event,
            usage=TokenUsage(
                prompt_tokens=int(prompt_tokens),
                output_tokens=int(output_tokens),
                cache_hit_tokens=int(cache_hit_tokens),
            ),
            time=fromtimestamp(float(created_at)).strftime("%Y年%m月%d日 %H:%M:%S"),
        )

    def get_all_usage(self) -> dict[str, list[TokenUsageData]]:
        rows = self._table.select_all(
            "model_name, event, prompt_tokens, output_tokens, cache_hit_tokens, created_at"
        )
        result: dict[str, list[TokenUsageData]] = {}
        for row in rows:
            usage_data = self._to_usage_data(row)
            result.setdefault(usage_data.name, []).append(usage_data)
        return result

    def get_usage(self, model_name: str) -> list[TokenUsageData]:
        rows = self._table.select(
            "model_name, event, prompt_tokens, output_tokens, cache_hit_tokens, created_at",
            {"model_name": model_name},
        )
        return [self._to_usage_data(row) for row in rows]

    def delete_usage_between(self, start_timestamp: float, end_timestamp: float) -> int:
        return self._table.delete(
            f"created_at >= {start_timestamp} AND created_at < {end_timestamp}"
        )


MTYM = ModelTokenUsageManager()
