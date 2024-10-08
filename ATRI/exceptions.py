import time
from pathlib import Path
from typing import Optional
from traceback import format_exc

from nonebot.matcher import Matcher
from nonebot.adapters.onebot.v11 import ActionFailed
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.message import run_postprocessor

from .log import log
from .message import MessageBuilder
from .utils import Limiter, gen_random_str
from .utils.model import BaseModel

ERROR_DIR = Path(".") / "data" / "errors"
ERROR_DIR.mkdir(parents=True, exist_ok=True)


class ErrorInfo(BaseModel):
    track_id: str
    prompt: str
    time: str
    content: str


def _save_error(prompt: str, content: str) -> str:
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
        self.track_id = _save_error(self.prompt, format_exc())
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


class GetStatusError(BaseBotException):
    prompt = "获取状态失败"


class FormatError(BaseBotException):
    prompt = "格式错误"


class ServiceRegisterError(BaseBotException):
    prompt = "服务注册错误"


class BilibiliDynamicError(BaseBotException):
    prompt = "b站动态订阅错误"


class TwitterDynamicError(BaseBotException):
    prompt = "Twitter动态订阅错误"


class ThesaurusError(BaseBotException):
    prompt = "词库相关错误"


class RssError(BaseBotException):
    prompt = "RSS订阅错误"


class ServiceNotFoundError(BaseBotException):
    prompt = "找不到指定服务"


class PluginError(BaseBotException):
    prompt = "插件错误"


limiter = Limiter(3, 600)


@run_postprocessor
async def _(bot: Bot, event, matcher: Matcher, exception: Optional[Exception]):
    if not exception:
        return

    if isinstance(exception, BaseBotException):
        exception: BaseBotException
        prompt = "机器人基本错误 " + exception.prompt or exception.__class__.__name__
        track_id = _save_error(prompt, format_exc())
        log.warning(f"BotException: {prompt}")
    elif isinstance(exception, ActionFailed):
        prompt = "发送错误 请参考协议端输出"
        track_id = _save_error(prompt, format_exc())
        log.warning(f"ActionFailed: {prompt}")
    elif isinstance(exception, Exception):
        prompt = "其他错误 " + exception.__class__.__name__
        track_id = _save_error(prompt, format_exc())
        log.warning(f"Exception: {prompt}")
    else:
        prompt = "未知错误 " + exception.__class__.__name__
        track_id = _save_error(prompt, format_exc())
        log.warning(f"Ignore Exception: {prompt}")

    log.error(f"Error Track ID: {track_id}")

    msg = (
        MessageBuilder("呜——出错了...请反馈维护者")
        .text(f"来自: {matcher.module_name}")
        .text(f"信息: {prompt}")
        .text(f"追踪ID: {track_id}")
    )
    if isinstance(event, GroupMessageEvent):
        group_id = str(event.group_id)
        if not limiter.check(group_id):
            msg = MessageBuilder("该群报错提示已达限制, 将冷却10min").text("如需反馈请: 来杯红茶")
        else:
            limiter.increase(group_id)

        if limiter.get_times(group_id) > 3:
            return

    try:
        await bot.send(event, msg)
    except Exception:
        return
