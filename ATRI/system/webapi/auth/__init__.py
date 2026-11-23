import nonebot
from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm

from ATRI import conf
from ATRI.log import log

from ..base_model import Result
from ..security import (
    LOGIN_RATE_LIMITER,
    TOKEN_REGISTRY,
    create_token,
    get_user,
    oauth2_scheme,
)

app = nonebot.get_app()


router = APIRouter()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/login")
async def login_get_token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
):
    ip = _client_ip(request)
    if LOGIN_RATE_LIMITER.is_locked(ip):
        log.warning(f"WebUI 登录请求被拒绝（锁定中）ip={ip}")
        return Result.fail("失败次数过多，请稍后再试", 996)
    username = conf.WebUIConfig.username
    password = conf.WebUIConfig.password
    if not username or not password:
        return Result.fail("你滴配置文件里用户名密码配置项为空", 998)
    if username != form_data.username or password != form_data.password:
        LOGIN_RATE_LIMITER.record_failure(ip)
        log.warning(f"WebUI 登录失败 ip={ip} user={form_data.username}")
        # 单用户系统：密码错误与用户不存在统一口径，不给探测空间
        return Result.fail("账号或密码错误", 999)
    user = get_user(form_data.username)
    if not user:
        LOGIN_RATE_LIMITER.record_failure(ip)
        log.warning(f"WebUI 登录失败 ip={ip} user={form_data.username}")
        return Result.fail("账号或密码错误", 999)
    LOGIN_RATE_LIMITER.record_success(ip)
    log.info(f"WebUI 登录成功 ip={ip} user={form_data.username}")
    access_token = create_token(user)
    TOKEN_REGISTRY.register(access_token)
    return Result.ok(
        {"access_token": access_token, "token_type": "bearer"}, "欢迎回家, 主人!"
    )


@router.post("/logout")
async def logout(token: str = Depends(oauth2_scheme)):
    """吊销当前 token，让「退出登录」真正生效"""
    raw_token = token[7:] if token.startswith("Bearer ") else token
    TOKEN_REGISTRY.revoke(raw_token)
    return Result.ok("下次再见, 主人!")


@router.get("/ping")
async def ping():
    return Result.ok("pong")
