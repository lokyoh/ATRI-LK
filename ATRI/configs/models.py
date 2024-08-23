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


class SauceNAO(BaseModel):
    key: str


class Setu(BaseModel):
    reverse_proxy: bool
    reverse_proxy_domain: str


class ConfigModel(BaseModel):
    ConfigVersion: str
    BotConfig: BotConfig
    SauceNAO: SauceNAO
    Setu: Setu


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
