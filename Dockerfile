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
ENV VIRTUAL_ENV=/app/venv
RUN python -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
# 关键：确保 Playwright 版本足够新（>=1.40），以支持 Debian 12
RUN poetry install --no-interaction --no-ansi --no-root --only main

# 下载 Playwright 浏览器到指定路径（不安装系统依赖）
ENV PLAYWRIGHT_BROWSERS_PATH=/app/ms-playwright
RUN playwright install chromium

# ------------------------------------------------------------
# 阶段2：最终运行镜像
FROM python:3.12-slim AS final

WORKDIR /app

# 从构建阶段复制虚拟环境
COPY --from=builder /app/venv /app/venv
ENV VIRTUAL_ENV=/app/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# 从构建阶段复制浏览器
COPY --from=builder /app/ms-playwright /app/ms-playwright
ENV PLAYWRIGHT_BROWSERS_PATH=/app/ms-playwright

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
    && rm -rf /var/lib/apt/lists/* /tmp/*

# 复制应用代码（最后复制以利用缓存）
COPY . .

ENV PYTHONPATH=/app

CMD ["python", "main.py"]