# 本镜像只提供**运行环境**：Python 依赖、Playwright 浏览器、启动脚本、对齐依赖
# 用的 poetry，全部放在 /opt 下。业务代码与数据由 docker-compose 挂进 /app，
# 两者互不覆盖，所以：
#
#   - 更新代码：不用重建镜像（代码在挂载目录里）；
#   - 依赖变化：启动脚本按 poetry.lock 自己补齐，也不用重建；
#   - 只有「系统层」变化才重建：系统包、Python 版本、Playwright、启动脚本、poetry。
#
# 依赖放在 /opt/atri-venv 而不是 /app/venv，是因为 compose 把宿主机仓库整个挂到
# /app——放 /app 里会被挂载遮住，或者反过来把虚拟环境写进宿主机仓库。

# 阶段1：构建环境（安装 Python 依赖并下载浏览器）
FROM python:3.12-slim AS builder

WORKDIR /app

# 安装构建时需要的系统工具（若不需要 git 可移除）
RUN apt-get update && apt-get install -y --no-install-recommends curl git \
    && rm -rf /var/lib/apt/lists/*

# 安装 Poetry（指定版本）
ENV POETRY_VERSION=2.3.2
RUN pip install --no-cache-dir "poetry==$POETRY_VERSION"

# 复制依赖文件（确保 pyproject.toml 和 poetry.lock 存在）
COPY pyproject.toml poetry.lock ./

# 创建虚拟环境并安装项目依赖（仅主依赖）
ENV VIRTUAL_ENV=/opt/atri-venv
RUN python -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
# 关键：确保 Playwright 版本足够新（>=1.40），以支持 Debian 12
RUN poetry install --no-interaction --no-ansi --no-root --only main

# 下载 Playwright 浏览器到指定路径（不安装系统依赖）
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN playwright install chromium

# 运行期对齐依赖用的 poetry：装在**独立**虚拟环境里。
# 不装进应用虚拟环境——poetry 自己的依赖会把 requests、urllib3 这类共享包抬到
# 别的版本，而 poetry install 又会把不在锁文件里的包清掉，两边互相拆台。
RUN python -m venv /opt/poetry-tool \
    && /opt/poetry-tool/bin/pip install --no-cache-dir "poetry==$POETRY_VERSION"

# 启动脚本属于运行环境，放 /opt 下——代码不打包进镜像，/app 留给挂载。
COPY scripts/docker_entrypoint.py /opt/atri/docker_entrypoint.py

# 记录依赖指纹：与本次构建装好的依赖对应。挂载进来的锁文件若与它一致，
# 启动时什么都不用做。
RUN python /opt/atri/docker_entrypoint.py --mark-only

# ------------------------------------------------------------
# 阶段2：最终运行镜像
FROM python:3.12-slim AS final

WORKDIR /app

# 虚拟环境与浏览器都在 /opt，不会被 /app 的挂载遮住
COPY --from=builder /opt/atri-venv /opt/atri-venv
ENV VIRTUAL_ENV=/opt/atri-venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY --from=builder /opt/ms-playwright /opt/ms-playwright
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

# 对齐依赖用的 poetry（独立环境，仅用于启动前安装）
COPY --from=builder /opt/poetry-tool /opt/poetry-tool

# 启动脚本：镜像只带运行环境，代码由宿主机挂载进 /app
COPY --from=builder /opt/atri /opt/atri

# 安装 Playwright 运行时所需的系统依赖
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl git \
    libglib2.0-0 \
    libnspr4 \
    libnss3 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libxcb1 \
    libxkbcommon0 \
    libatspi2.0-0 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libcairo2 \
    libpango-1.0-0 \
    libasound2 \
    fonts-wqy-zenhei \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* /tmp/*

ENV PYTHONPATH=/app

# 启动前按 poetry.lock 对齐依赖，然后再起应用。
# 用镜像自带的 python，不依赖 /opt/atri-venv 是否被挂载遮住，也不依赖 /app 里
# 有没有代码。
ENV ATRI_ENTRYPOINT=/opt/atri/docker_entrypoint.py
ENTRYPOINT ["/usr/local/bin/python", "/opt/atri/docker_entrypoint.py"]
CMD ["python", "main.py"]
