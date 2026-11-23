from enum import StrEnum

import nonebot
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from ATRI import conf

app = nonebot.get_app()

# 仅在用户显式配置了允许来源时注册 CORS：
# 生产同源部署不需要 CORS；["*"] + allow_credentials 的组合会让任意
# 网站带凭据跨域调用 API，绝不可用
allowed_origins = conf.WebUIConfig.allowed_origins

if (
    allowed_origins
    and app.middleware_stack is None
    and not any(middleware.cls is CORSMiddleware for middleware in app.user_middleware)
):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# REST 响应与静态资源 gzip 压缩（历史日志一次 500 行、Element 样式与
# echarts chunk 都明显可压）；反代部署时也可由 Nginx 统一承担
if app.middleware_stack is None:
    app.add_middleware(GZipMiddleware, minimum_size=1024)


AVA_URL = "http://q1.qlogo.cn/g?b=qq&nk={}&s=160"

GROUP_AVA_URL = "http://p.qlogo.cn/gh/{}/{}/640/"


class QueryDateType(StrEnum):
    """
    查询日期类型
    """

    DAY = "day"
    """日"""
    WEEK = "week"
    """周"""
    MONTH = "month"
    """月"""
    YEAR = "year"
    """年"""
