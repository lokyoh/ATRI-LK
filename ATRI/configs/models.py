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
    timezone: str = "Asia/Shanghai"


class BrowsConfig(BaseModel):
    browser: str
    download_host: str
    proxy_host: str
    browser_channel: str


class WebUIConfig(BaseModel):
    username: str = "admin"
    password: str = Field(default_factory=lambda: gen_random_str(8))
    # JWT 签名密钥：32 位字母数字（约 190 bits），公网部署下抵御离线爆破
    secret: str = Field(default_factory=lambda: gen_random_str(32))
    # CORS 允许的来源；默认仅本地开发端口。生产同源部署保持为空即可，
    # 前端单独部署时显式配置（禁止 ["*"] + credentials 的组合）
    allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )


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
