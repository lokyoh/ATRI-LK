import time
import traceback
from pathlib import Path
from typing import Optional

from ATRI.utils import gen_random_str
from ATRI.utils.model import BaseModel

ERROR_DIR = Path(".") / "data" / "errors"
ERROR_DIR.mkdir(parents=True, exist_ok=True)


class ErrorInfo(BaseModel):
    track_id: str
    prompt: str
    time: str
    content: str


def save_error(prompt: str, content: str) -> str:
    track_id = gen_random_str(8)
    data = ErrorInfo(
        track_id=track_id,
        prompt=prompt,
        time=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        content=content,
    )
    path = ERROR_DIR / f"{track_id}.json"
    data.write_into_file(path)
    return track_id


def load_error(track_id: str) -> ErrorInfo:
    path = ERROR_DIR / f"{track_id}.json"
    return ErrorInfo.read_from_file(path)


class BaseBotException(Exception):
    prompt: Optional[str] = "ignore"

    def __init__(self, prompt: Optional[str]) -> None:
        self.prompt = prompt or self.__class__.prompt or self.__class__.__name__
        super().__init__(self.prompt)


class NotConfigured(BaseBotException):
    prompt = "缺少配置"


class InvalidConfigured(BaseBotException):
    prompt = "无效配置"


class WriteFileError(BaseBotException):
    prompt = "写入错误"


class ReadFileError(BaseBotException):
    prompt = "读取文件失败"


class RequestError(BaseBotException):
    prompt = "网页/接口请求错误"


class FormatError(BaseBotException):
    prompt = "格式错误"


class ServiceRegisterError(BaseBotException):
    prompt = "服务注册错误"


class ServiceNotFoundError(BaseBotException):
    prompt = "找不到指定服务"


class PluginError(BaseBotException):
    prompt = "插件错误"


class BotRuntimeError(BaseBotException):
    prompt = "机器人运行时错误"


class EventRuntimeError(BaseBotException):
    prompt = "事件运行错误"

    def __init__(self, prompt: str, content: str) -> None:
        self.content = content
        super().__init__(prompt)


def str_traceback(e) -> str:
    """
    获取错误的精简追踪信息。
    :param e: 错误对象
    :return: 精简后的错误信息文本
    """
    return _str_traceback(traceback.format_exception(type(e), e, e.__traceback__))


def _str_traceback(traceback_msg: list) -> str:
    filtered_lines = [traceback_msg[0]]
    for line in traceback_msg[1:-1]:
        if "site-packages" not in line:
            filtered_lines.append(line)
    filtered_lines.append(traceback_msg[-1])
    return "".join(filtered_lines)
