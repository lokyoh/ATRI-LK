from typing import Annotated
from pydantic import Field

from ..utils import gen_random_str
from ..utils.model import BaseModel


class BotConfig(BaseModel):
    host: str
    port: int
    debug: bool
    superusers: list
    nickname: list
    command_start: list
    command_sep: list
    session_expire_timeout: int
    access_token: str
    proxy: str
    request_timeout: int


class BrowsConfig(BaseModel):
    browser: str
    download_host: str
    proxy_host: str
    browser_channel: str


class WebUIConfig(BaseModel):
    username: str = 'admin'
    password: str = Field(default_factory=lambda: gen_random_str(8))
    secret: str = Field(default_factory=lambda: gen_random_str(8))


class ConfigModel(BaseModel):
    ConfigVersion: str
    BotConfig: BotConfig
    BrowsConfig: BrowsConfig
    WebUIConfig: Annotated[WebUIConfig, Field(default=WebUIConfig())]


class RuntimeConfig(BaseModel):
    host: str
    port: int
    debug: bool
    superusers: list
    nickname: list
    onebot_access_token: str
    command_start: list
    command_sep: list
    session_expire_timeout: int
