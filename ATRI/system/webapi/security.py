"""WebUI 安全核心：JWT 签发/校验、token 登记簿（吊销）、登录限速、WS 握手鉴权。

从 utils.py 拆出：auth 是安全敏感且持续生长的部分，边界应独立。
纯逻辑类（TokenRegistry / LoginRateLimiter）不依赖框架，可脱离 nonebot 单测。
"""

import asyncio
import contextlib
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import Depends, HTTPException, Response
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from starlette.websockets import WebSocket, WebSocketDisconnect

from ATRI import conf
from ATRI.dir import DATA_DIR

from .base_model import User

ALGORITHM = "HS256"
# 公网部署下 token 取 2h：配合吊销名单与滑动续期，
# 不活跃即过期，活跃客户端无感续签；如转回纯内网可放宽到 24h
ACCESS_TOKEN_EXPIRE_MINUTES = 120
TOKEN_REGISTRY_LIMIT = 5
"""已签发 token 登记簿上限，超出淘汰最旧（滑动续期会持续签发新 token）"""
WS_AUTH_TIMEOUT = 5
"""WebSocket 握手鉴权超时：连接建立后须在此时间内发出含 token 的首条消息"""

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/login")


class TokenRegistry:
    """已签发 token 登记簿（吊销白名单），持久化到 JSON 文件。

    超出 limit 时淘汰最旧：滑动续期会持续签发新 token，旧的由
    容量淘汰和自身过期自然失效。
    """

    def __init__(
        self,
        path: Path,
        limit: int = TOKEN_REGISTRY_LIMIT,
        load: bool = True,
    ):
        self.path = path
        self.limit = limit
        self.tokens: list[str] = []
        if load:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data: dict = {}
            if self.path.exists():
                with (
                    contextlib.suppress(json.JSONDecodeError, OSError),
                    self.path.open(encoding="utf8") as f,
                ):
                    data = json.load(f)
            tokens = data.get("token") if isinstance(data, dict) else None
            if isinstance(tokens, list):
                self.tokens = [t for t in tokens if isinstance(t, str)]

    def contains(self, token: str) -> bool:
        return token in self.tokens

    def register(self, token: str) -> None:
        self.tokens.append(token)
        if len(self.tokens) > self.limit:
            self.tokens = self.tokens[1:]
        self._persist()

    def revoke(self, token: str) -> None:
        if token in self.tokens:
            self.tokens.remove(token)
            self._persist()

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf8") as f:
            json.dump({"token": self.tokens}, f, ensure_ascii=False, indent=4)
        # 文件里是明文的有效 token，POSIX 系统收紧权限；Windows 下忽略
        with contextlib.suppress(OSError):
            self.path.chmod(0o600)


TOKEN_REGISTRY = TokenRegistry(DATA_DIR / "webui" / "token.json")


class LoginRateLimiter:
    """登录失败限速：按来源 IP 滑动窗口计数，失败过多锁定一段时间。"""

    def __init__(
        self,
        window: float = 60.0,
        max_failures: int = 5,
        lock_duration: float = 600.0,
        clock: Callable[[], float] | None = None,
    ):
        self.window = window
        self.max_failures = max_failures
        self.lock_duration = lock_duration
        self._clock = clock or time.time
        self._failures: dict[str, list[float]] = {}
        self._locks: dict[str, float] = {}

    def is_locked(self, ip: str) -> bool:
        locked_until = self._locks.get(ip)
        if locked_until is None:
            return False
        if self._clock() >= locked_until:
            self._locks.pop(ip, None)
            self._failures.pop(ip, None)
            return False
        return True

    def record_failure(self, ip: str) -> None:
        now = self._clock()
        attempts = [
            t for t in self._failures.get(ip, []) if now - t < self.window
        ]
        attempts.append(now)
        self._failures[ip] = attempts
        if len(attempts) >= self.max_failures:
            self._locks[ip] = now + self.lock_duration

    def record_success(self, ip: str) -> None:
        self._failures.pop(ip, None)
        self._locks.pop(ip, None)


LOGIN_RATE_LIMITER = LoginRateLimiter()


def get_user(uname: str) -> User | None:
    """获取账号密码

    参数:
        uname: uname

    返回:
        Optional[User]: 用户信息
    """
    username = conf.WebUIConfig.username
    password = conf.WebUIConfig.password
    if username and password and uname == username:
        return User(username=username, password=password)


def create_token(user: User, expires_delta: timedelta | None = None):
    """创建token

    参数:
        user: 用户信息
        expires_delta: 过期时间，缺省用 ACCESS_TOKEN_EXPIRE_MINUTES.
    """
    expire = datetime.now(UTC) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    return jwt.encode(
        claims={"sub": user.username, "exp": expire},
        key=conf.WebUIConfig.secret,
        algorithm=ALGORITHM,
    )


def verify_token(token: str | None) -> str | None:
    """校验 JWT + 吊销名单，成功返回用户名，失败返回 None

    参数:
        token: JWT，兼容带 "Bearer " 前缀的完整值（WebSocket 握手场景）
    """
    if not token:
        return None
    if token.startswith("Bearer "):
        token = token[7:]
    try:
        payload = jwt.decode(token, conf.WebUIConfig.secret, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username or not get_user(username):
            return None
    except JWTError:
        return None
    # 吊销校验：必须在已签发名单里（退出登录 / 被淘汰即失效）
    if not TOKEN_REGISTRY.contains(token):
        return None
    return username


def authentication():
    """权限验证 + 滑动续期

    token 剩余寿命不足一半时签发新 token，经 X-Refreshed-Token
    响应头下发给前端换 cookie，活跃用户无感续签、不活跃自然过期。

    异常:
        HTTPException: 登录验证失败或已失效
    """

    def inner(response: Response, token: str = Depends(oauth2_scheme)):
        if verify_token(token) is None:
            raise HTTPException(
                status_code=400, detail="登录验证失败或已失效, 踢出房间!"
            )

        # 滑动续期：JWT 有效（verify_token 已校验），此处只看剩余寿命
        raw_token = token[7:] if token.startswith("Bearer ") else token
        claims = jwt.decode(raw_token, conf.WebUIConfig.secret, algorithms=[ALGORITHM])
        exp = claims.get("exp")
        if not exp:
            return
        remaining = exp - datetime.now(UTC).timestamp()
        if remaining >= ACCESS_TOKEN_EXPIRE_MINUTES * 60 / 2:
            return
        user = get_user(claims.get("sub"))  # type: ignore
        if user is None:
            return
        # 新 token 只登记不吊销旧 token：并发请求可能仍持旧 token，
        # 旧的由名单容量淘汰和自身过期自然失效
        new_token = create_token(user)
        TOKEN_REGISTRY.register(new_token)
        response.headers["X-Refreshed-Token"] = new_token

    return Depends(inner)


async def _ws_reject(websocket: WebSocket, reason: str) -> None:
    # 对端可能已断开，关闭失败不必传播
    with contextlib.suppress(Exception):
        await websocket.close(code=1008, reason=reason)


async def ws_auth(websocket: WebSocket) -> dict | None:
    """WebSocket 首条消息握手鉴权。

    连接 accept 后，客户端必须在超时时间内发送 JSON 首条消息，
    其中 "token" 字段须为有效 JWT（可附带其他字段，如日志续传的 since）。

    返回:
        dict | None: 鉴权通过时返回解析后的首条消息；失败时返回 None（连接已关闭），
        调用方应直接 return，不再继续使用该连接。
    """
    await websocket.accept()
    try:
        first = await asyncio.wait_for(websocket.receive_text(), WS_AUTH_TIMEOUT)
        data = json.loads(first)
        if not isinstance(data, dict):
            raise ValueError
    except (TimeoutError, WebSocketDisconnect, ValueError):
        await _ws_reject(websocket, "handshake timeout or invalid payload")
        return None
    if not verify_token(data.get("token")):
        await _ws_reject(websocket, "unauthorized")
        return None
    return data
