import json
import os
from pydantic import BaseModel as PBaseModel


class BaseModel(PBaseModel):
    @classmethod
    def read_from_file(cls, path):
        """类方法，返回一个从指定文件读取类对象"""
        if not os.path.exists(path):
            raise IOError(
                "找不到指定文件"
            )
        with open(path, 'r', encoding='utf-8') as file:
            model = cls.model_validate(json.load(file))
        return model

    def write_into_file(self, path):
        with open(path, 'w', encoding='utf-8') as file:
            json.dump(self.model_dump(), file, indent=4, ensure_ascii=False)
