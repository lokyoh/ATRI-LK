from ..utils.model import BaseModel


class BotConfig(BaseModel):
    host: str
    port: int
    debug: bool
    superusers: set
    nickname: set
    command_start: set
    command_sep: set
    session_expire_timeout: int
    access_token: str
    proxy: str
    request_timeout: int


class BrowsConfig(BaseModel):
    browser: str
    download_host: str
    proxy_host: str
    browser_channel: str


class ConfigModel(BaseModel):
    ConfigVersion: str
    BotConfig: BotConfig
    BrowsConfig: BrowsConfig


class RuntimeConfig(BaseModel):
    host: str
    port: int
    debug: bool
    superusers: set
    nickname: set
    onebot_access_token: str
    command_start: set
    command_sep: set
    session_expire_timeout: int
